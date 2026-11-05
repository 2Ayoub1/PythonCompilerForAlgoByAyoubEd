import sys
import os
import re
import keyword
import subprocess

# ---------------------------------------------------------------------------
# Outils pour le passage par adresse (paramètres 'var')
# ---------------------------------------------------------------------------
# En Python, les listes sont passées par référence (donc 'var T[]' marchait), mais
# les entiers / booléens / chaînes sont immuables : 'running <- Faux' dans une
# routine ne modifie que sa variable locale. Solution : la routine publie la valeur
# finale de ses paramètres 'var' scalaires (via _ret), et l'appelant les recopie
# dans ses propres variables juste après l'appel.

def split_top_level(s, sep=','):
    """Découpe s sur sep, sans couper à l'intérieur de (), [], {} ou des guillemets."""
    parts, cur, depth, quote = [], [], 0, None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
        elif ch in '"\'':
            quote = ch
        elif ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(''.join(cur))
            cur = []
            continue
        cur.append(ch)
    parts.append(''.join(cur))
    return parts

def split_call(stmt):
    """Si stmt est exactement un appel 'Nom(a, b, ...)', renvoie (Nom, [a, b, ...]), sinon None."""
    stmt = stmt.strip()
    m = re.match(r'^([A-Za-z_]\w*)\s*\(', stmt)
    if not m or not stmt.endswith(')'):
        return None
    start = m.end() - 1
    depth, quote = 0, None
    for k in range(start, len(stmt)):
        ch = stmt[k]
        if quote:
            if ch == quote:
                quote = None
        elif ch in '"\'':
            quote = ch
        elif ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
            if depth == 0:
                if k != len(stmt) - 1:
                    return None  # ex: F(a) + G(b) : ce n'est pas un appel seul
                inner = stmt[start + 1:k]
                args = [a.strip() for a in split_top_level(inner)] if inner.strip() else []
                return m.group(1), args
    return None

def is_lvalue(expr):
    """Vrai si expr est une variable ou un élément de tableau (x, T[i], M[i][j]),
    c'est-à-dire quelque chose à qui on peut affecter une valeur."""
    expr = expr.strip()
    m = re.match(r'^[A-Za-z_]\w*', expr)
    if not m or keyword.iskeyword(m.group(0)):  # exclut True / False / None
        return False
    rest = expr[m.end():]
    while rest:
        if rest[0] != '[':
            return False
        depth, end = 0, -1
        for k, ch in enumerate(rest):
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    end = k
                    break
        if end < 0:
            return False
        rest = rest[end + 1:]
    return True

def parse_params(params_raw):
    """'var T[] : Reel, d : Entier' -> [{'name': 'T', 'is_var': True, 'is_array': True}, ...]"""
    params = []
    if params_raw.strip():
        for p in split_top_level(params_raw):
            arg_name_full = p.split(':')[0].strip()
            is_var = bool(re.match(r'^var\s+', arg_name_full, re.IGNORECASE))
            arg_name = re.sub(r'^var\s+', '', arg_name_full, flags=re.IGNORECASE).strip()
            is_array = '[' in arg_name
            arg_name = re.sub(r'\[.*?\]', '', arg_name).strip()
            if arg_name:
                params.append({'name': arg_name, 'is_var': is_var, 'is_array': is_array})
    return params

def collect_ref_signatures(lines):
    """Première passe : repère les routines qui ont au moins un paramètre 'var' scalaire
    (pas un tableau). Renvoie {nom_routine: [positions de ces paramètres]}."""
    sigs = {}
    for raw in lines:
        l = raw.split('//')[0].strip()
        m = re.match(r'^(fonction|proc[eé]dure)\s+([a-zA-Z0-9_]+)\s*\((.*?)\)', l, re.IGNORECASE)
        if m:
            idx = [k for k, p in enumerate(parse_params(m.group(3)))
                   if p['is_var'] and not p['is_array']]
            if idx:
                sigs[m.group(2)] = idx
    return sigs

def collect_main_vars(lines):
    """Première passe : noms des variables déclarées dans l'algorithme principal
    (ce sont les variables globales du programme Python généré)."""
    names, in_main, in_vars = set(), False, False
    for raw in lines:
        l = raw.split('//')[0].strip()
        if not l:
            continue
        low = l.lower()
        if re.match(r'^(?:programme|algorithme)\b', low):
            in_main, in_vars = True, False
            continue
        if not in_main:
            continue
        if re.match(r'^(?:variables?|var)\s*:?$', low):
            in_vars = True
            continue
        if re.match(r'^d[eé]but$', low):
            break
        if in_vars and ':' in l:
            for decl in l.split(':')[0].split(','):
                name = re.split(r'<-+|=', decl)[0].split('[')[0].strip()
                if name:
                    names.add(name)
    return names

def transpile_to_python(source_path):
    with open(source_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    py_lines = []
    
    # Injection des fonctions utilitaires
    py_lines.append("_input_buffer = []")
    py_lines.append("def _lire_safe():")
    py_lines.append("    global _input_buffer")
    py_lines.append("    while not _input_buffer:")
    py_lines.append("        _input_buffer.extend(input().split())")
    py_lines.append("    val = _input_buffer.pop(0)")
    py_lines.append("    try: return int(val)")
    py_lines.append("    except ValueError:")
    py_lines.append("        try: return float(val)")
    py_lines.append("        except ValueError: return val")
    py_lines.append("")
    py_lines.append("def _redim(t, new_size, default_val=0):")
    py_lines.append("    if len(t) < new_size:")
    py_lines.append("        t.extend([default_val] * (new_size - len(t)))")
    py_lines.append("    else:")
    py_lines.append("        del t[new_size:]")
    py_lines.append("")
    # Passage par adresse : _ret() publie les valeurs finales des paramètres 'var'
    py_lines.append("_ref_out = ()")
    py_lines.append("def _ret(result, *refs):")
    py_lines.append("    global _ref_out")
    py_lines.append("    _ref_out = refs")
    py_lines.append("    return result")
    py_lines.append("")

    indent_level = 0
    in_variables = False
    in_debut = False
    declared_vars = set()
    switch_stack = []
    ref_sigs = collect_ref_signatures(lines)   # routines ayant des 'var' scalaires
    main_vars = collect_main_vars(lines)       # variables globales du programme
    current_func = None                        # routine en cours de transpilation

    def get_indent():
        return "    " * indent_level

    def clean_expr(expr):
        parts = expr.split('"')
        for idx_part in range(0, len(parts), 2):
            e = parts[idx_part]
            e = re.sub(r'\bmod\b', '%', e, flags=re.IGNORECASE)
            e = re.sub(r'\bdiv\b', '//', e, flags=re.IGNORECASE)
            e = re.sub(r'\bvrai\b', 'True', e, flags=re.IGNORECASE)
            e = re.sub(r'\bfaux\b', 'False', e, flags=re.IGNORECASE)
            e = re.sub(r'\b(et|and)\b', 'and', e, flags=re.IGNORECASE)
            e = re.sub(r'\b(ou|or)\b', 'or', e, flags=re.IGNORECASE)
            e = re.sub(r'\b(non|not)\b', 'not', e, flags=re.IGNORECASE)
            parts[idx_part] = e
        return '"'.join(parts)

    def clean_cond(cond):
        c = cond.strip()
        if c.endswith(':'):
            c = c[:-1].strip()
        c = c.replace('<=', '§LE§').replace('>=', '§GE§').replace('<>', '§NE§').replace('!=', '§NE§')
        c = re.sub(r'(?<![<>!])=(?!=)', '==', c)
        c = c.replace('§LE§', '<=').replace('§GE§', '>=').replace('§NE§', '!=')
        return clean_expr(c)

    def ref_call_lines(stmt):
        """Appel d'une routine qui a des paramètres 'var' scalaires, soit seul : P(x),
        soit affecté : y <- F(x). Après l'appel, on recopie les valeurs finales
        publiées par la routine (_ref_out) dans les variables passées par adresse.
        Renvoie les lignes Python à écrire, ou None si stmt n'est pas un tel appel."""
        stmt = stmt.strip()
        lhs = None
        call = split_call(stmt)
        if call is None:
            parts = re.split(r'<-+', stmt, maxsplit=1)
            if len(parts) == 2 and is_lvalue(parts[0]):
                lhs = parts[0].strip()
                call = split_call(parts[1])
        if call is None or call[0] not in ref_sigs:
            return None

        name, args = call
        args = [clean_expr(a) for a in args]
        targets = []
        for k in ref_sigs[name]:
            # argument non modifiable (constante, expression) : on ignore la valeur
            t = args[k] if k < len(args) and is_lvalue(args[k]) else '_'
            targets.append(t)
            # Dans une routine, une variable de l'algorithme principal qu'on n'a pas
            # redéclarée localement est une variable globale : il faut le dire à Python.
            if (current_func and re.fullmatch(r'[A-Za-z_]\w*', t)
                    and t in main_vars and t not in current_func['locals']):
                current_func['globals'].add(t)

        ind = get_indent()
        call_txt = f"{name}({', '.join(args)})"
        if len(targets) == 1:
            copy_back = f"{targets[0]} = _ref_out[0]"
        else:
            copy_back = f"{', '.join(targets)} = _ref_out"
        if lhs is None:
            return [f"{ind}{call_txt}", f"{ind}{copy_back}"]
        # y <- F(...) : on garde le résultat de côté pour que y gagne si y est aussi un 'var'
        return [f"{ind}_rv = {call_txt}", f"{ind}{copy_back}", f"{ind}{lhs} = _rv"]

    body_py_lines = []

    for i, raw_line in enumerate(lines):
        original_line = raw_line.strip()
        if '//' in original_line:
            original_line = original_line.split('//')[0].strip()
        
        if not original_line:
            continue
            
        line = original_line
        if line.endswith(':'):
            line = line[:-1].strip()

        next_line = ""
        for j in range(i + 1, len(lines)):
            nl = lines[j].strip()
            if '//' in nl:
                nl = nl.split('//')[0].strip()
            if nl:
                next_line = nl
                break

        lower_line = line.lower()

        if re.match(r'^(?:programme|algorithme)\b', lower_line):
            continue
        
        m_func = re.match(r'^(fonction|proc[eé]dure)\s+([a-zA-Z0-9_]+)\s*\((.*?)\)(?:\s*:\s*[a-zA-Z0-9_]+)?', line, re.IGNORECASE)
        if m_func:
            func_name = m_func.group(2)
            params = parse_params(m_func.group(3))
            args = [p['name'] for p in params]
            val_args = [p['name'] for p in params if not p['is_var']]
            # paramètres 'var' scalaires : à renvoyer à l'appelant (voir _ret)
            ref_args = [p['name'] for p in params if p['is_var'] and not p['is_array']]

            args_str = ", ".join(args)
            body_py_lines.append(f"{get_indent()}def {func_name}({args_str}):")
            indent_level += 1

            current_func = {
                'refs': ref_args,
                'locals': set(args),          # paramètres + variables déclarées dans la routine
                'globals': set(),             # variables globales à déclarer 'global'
                'indent': get_indent(),
                'slot': len(body_py_lines),   # emplacement réservé pour la ligne 'global ...'
            }
            body_py_lines.append(None)

            for v_arg in val_args:
                body_py_lines.append(f"{get_indent()}{v_arg} = __import__('copy').deepcopy({v_arg})")
                
            in_variables = False
            continue

        if re.match(r'^fin\s*(?:fonction|proc[eé]dure)$', re.sub(r'\s+', '', lower_line)):
            if current_func:
                if current_func['refs']:
                    # fin de routine : on publie les valeurs finales des paramètres 'var'
                    body_py_lines.append(f"{current_func['indent']}return _ret(None, {', '.join(current_func['refs'])})")
                if current_func['globals']:
                    body_py_lines[current_func['slot']] = f"{current_func['indent']}global {', '.join(sorted(current_func['globals']))}"
                current_func = None
            indent_level = max(0, indent_level - 1)
            continue

        m_ret = re.match(r'^retourner\s*\((.*?)\)$', line, re.IGNORECASE)
        if not m_ret:
            m_ret = re.match(r'^retourne\s+(.+)$', line, re.IGNORECASE)
        if m_ret:
            if current_func and current_func['refs']:
                body_py_lines.append(f"{get_indent()}return _ret({clean_expr(m_ret.group(1))}, {', '.join(current_func['refs'])})")
            else:
                body_py_lines.append(f"{get_indent()}return {clean_expr(m_ret.group(1))}")
            continue

        if re.match(r'^(?:variables?|var)\s*:?$', lower_line):
            in_variables = True
            continue
        
        if re.match(r'^d[eé]but$', lower_line):
            in_variables = False
            in_debut = True
            if indent_level == 0:
                for v_auto in ['r', 'temp']:
                    if v_auto not in declared_vars:
                        body_py_lines.append(f"{v_auto} = 0")
            continue
        
        if lower_line == 'fin':
            break

        if in_variables:
            if ':' in line:
                parts = line.split(':')
                left_side = parts[0].strip()
                var_type = parts[1].strip().lower()
                
                if re.match(r'^(bool[eé]en|bool|boolean)', var_type):
                    default_val = "False"
                # NOUVEAU : Support ultra-large pour les chaînes et caractères
                elif re.match(r'^(cha[iî]ne|caract|string|texte|lettre)', var_type):
                    default_val = '""'
                else:
                    default_val = "0"
                    
                var_declarations = [v.strip() for v in left_side.split(',') if v.strip()]
                
                for v_decl in var_declarations:
                    m_assign = re.search(r'(<-+|=)\s*(.+)', v_decl)
                    if m_assign:
                        v_name_raw = v_decl[:m_assign.start()].strip()
                        v_val = clean_expr(m_assign.group(2).strip())
                    else:
                        v_name_raw = v_decl
                        v_val = default_val
                        
                    clean_v_name = v_name_raw.split('[')[0].strip()
                    
                    if clean_v_name.lower() == 'input':
                        continue
                        
                    declared_vars.add(clean_v_name)
                    if current_func:
                        current_func['locals'].add(clean_v_name)
                    
                    dims = []
                    if '[' in v_name_raw:
                        dims_raw = re.findall(r'\[(.*?)\]', v_name_raw[v_name_raw.find('['):])
                        if dims_raw:
                            dims = [d.strip() for d in dims_raw[0].split(',')] 
                    elif 'tableau' in var_type and '[' in var_type:
                        m_dim = re.search(r'\[(.*?)\]', var_type)
                        if m_dim:
                            dims = [d.strip() for d in m_dim.group(1).split(',')]

                    if dims:
                        if len(dims) == 1:
                            d1 = dims[0] if dims[0] else "0"
                            body_py_lines.append(f"{get_indent()}{clean_v_name} = [{v_val}] * ({d1})")
                        elif len(dims) == 2:
                            d1 = dims[0] if dims[0] else "0"
                            d2 = dims[1] if dims[1] else "0"
                            body_py_lines.append(f"{get_indent()}{clean_v_name} = [[{v_val}] * ({d2}) for _ in range({d1})]")
                        else:
                            body_py_lines.append(f"{get_indent()}{clean_v_name} = []")
                    else:
                        body_py_lines.append(f"{get_indent()}{clean_v_name} = {v_val}")
            continue

        if in_debut:
            if re.match(r'^fin\s*_?\s*(pour|si|tant\s*que)$', lower_line):
                indent_level = max(0, indent_level - 1)
                continue

            if lower_line in ['sortir', 'quitter', 'interrompre']:
                body_py_lines.append(f"{get_indent()}break")
                continue

            m_cas = re.match(r'^cas\s*\((.+?)\)\s*vaut\s*:?$', line, re.IGNORECASE)
            if m_cas:
                switch_stack.append({"var": clean_expr(m_cas.group(1)), "first": True, "in_case": False})
                continue

            if re.match(r'^fincas$', lower_line):
                if switch_stack:
                    curr_switch = switch_stack.pop()
                    if curr_switch.get("in_case"):
                        indent_level = max(0, indent_level - 1)
                continue
                
            if switch_stack:
                parts = split_top_level(original_line, sep=':')
                if len(parts) >= 2 and not re.search(r'<-+', original_line):
                    val_part = parts[0].strip()
                    instr_part = ':'.join(parts[1:]).strip()
                    current_switch = switch_stack[-1]
                    
                    if current_switch.get("in_case"):
                        indent_level = max(0, indent_level - 1)
                    
                    current_switch["in_case"] = True
                    
                    if val_part.lower() == 'sinon':
                        body_py_lines.append(f"{get_indent()}else:")
                    else:
                        if current_switch["first"]:
                            body_py_lines.append(f"{get_indent()}if {current_switch['var']} == {clean_expr(val_part)}:")
                            current_switch["first"] = False
                        else:
                            body_py_lines.append(f"{get_indent()}elif {current_switch['var']} == {clean_expr(val_part)}:")
                    
                    indent_level += 1
                    
                    if instr_part:
                        ref_lines = ref_call_lines(instr_part)
                        if ref_lines:
                            body_py_lines.extend(ref_lines)
                        elif re.search(r'<-+', instr_part):
                            p = re.split(r'<-+', instr_part, maxsplit=1)
                            body_py_lines.append(f"{get_indent()}{p[0].strip()} = {clean_expr(p[1].strip())}")
                        else:
                            m_print = re.match(r'^(?:[eé]crire|afficher)\s*\((.*?)\)$', instr_part, re.IGNORECASE)
                            m_lire = re.match(r'^lire\s*\((.+)\)$', instr_part, re.IGNORECASE)
                            m_inc = re.match(r'^([a-zA-Z0-9_\[\]]+)\s*\+\+$', instr_part)
                            m_dec = re.match(r'^([a-zA-Z0-9_\[\]]+)\s*--$', instr_part)
                            
                            if m_print:
                                body_py_lines.append(f"{get_indent()}print({clean_expr(m_print.group(1))})")
                            elif m_lire:
                                vars_to_read = [v.strip() for v in m_lire.group(1).split(',')]
                                for v in vars_to_read:
                                    body_py_lines.append(f"{get_indent()}{v} = _lire_safe()")
                            elif m_inc:
                                body_py_lines.append(f"{get_indent()}{m_inc.group(1)} += 1")
                            elif m_dec:
                                body_py_lines.append(f"{get_indent()}{m_dec.group(1)} -= 1")
                            else:
                                instr_cleaned = re.sub(r'(?i)\bajouter\s*\(\s*([^,]+)\s*,\s*(.+?)\s*\)', r'\1.append(\2)', instr_part)
                                instr_cleaned = re.sub(r'(?i)\b([a-zA-Z0-9_]+)\.ajouter\s*\(\s*(.+?)\s*\)', r'\1.append(\2)', instr_cleaned)
                                body_py_lines.append(f"{get_indent()}{clean_expr(instr_cleaned)}")
                    continue

            m = re.match(r'^sinon\s+si\s*\((.+?)\)(?:\s+alors)?$', line, re.IGNORECASE)
            if not m:
                m = re.match(r'^sinon\s+si\s+(.+?)\s+alors$', line, re.IGNORECASE)
            if m:
                indent_level = max(0, indent_level - 1)
                body_py_lines.append(f"{get_indent()}elif {clean_cond(m.group(1))}:")
                indent_level += 1
                continue

            if lower_line.startswith('sinon'):
                indent_level = max(0, indent_level - 1)
                inline_stmt = line[5:].strip()
                body_py_lines.append(f"{get_indent()}else:")
                indent_level += 1
                if inline_stmt:
                    ref_lines = ref_call_lines(inline_stmt)
                    if ref_lines:
                        body_py_lines.extend(ref_lines)
                    elif re.search(r'<-+', inline_stmt):
                        p = re.split(r'<-+', inline_stmt, maxsplit=1)
                        body_py_lines.append(f"{get_indent()}{p[0].strip()} = {clean_expr(p[1].strip())}")
                    else:
                        body_py_lines.append(f"{get_indent()}{clean_expr(inline_stmt)}")
                continue

            m = re.match(r'^si\s*\((.+?)\)(?:\s+alors)?$', line, re.IGNORECASE)
            if not m:
                m = re.match(r'^si\s+(.+?)\s+alors$', line, re.IGNORECASE)
            if m:
                body_py_lines.append(f"{get_indent()}if {clean_cond(m.group(1))}:")
                indent_level += 1
                continue

            ref_lines = ref_call_lines(line)
            if ref_lines:
                body_py_lines.extend(ref_lines)
                continue

            m = re.match(r'^(?:pour\s+)?([a-zA-Z0-9_]+)\s*(?:<-+|=)\s*(.+?)\s+((?:a|à)|(?:jusqu[\' ]*[aà]))\s+(.+?)(?:\s+pas\s+([+-]?[0-9]+))?(?:\s+faire)?$', line, re.IGNORECASE)
            if m:
                var, start, _unused, end, step = m.groups()
                if step:
                    body_py_lines.append(f"{get_indent()}for {var} in range(int({start}), int({end}) + (1 if int({step}) > 0 else -1), int({step})):")
                else:
                    body_py_lines.append(f"{get_indent()}for {var} in range(int({start}), int({end}) + 1):")
                indent_level += 1
                continue

            m = re.match(r'^(?:tant\s*que|pendant\s*que)\s+(.+?)(?:\s+faire)?$', line, re.IGNORECASE)
            if m:
                body_py_lines.append(f"{get_indent()}while {clean_cond(m.group(1))}:")
                indent_level += 1
                continue

            if re.match(r'^r[eé]p[eé]ter$', lower_line):
                body_py_lines.append(f"{get_indent()}while True:")
                indent_level += 1
                continue

            m = re.match(r'^jusqu[\'\s]*[aà]\s*\((.+?)\)$', line, re.IGNORECASE)
            if m:
                body_py_lines.append(f"{get_indent()}if {clean_cond(m.group(1))}:")
                body_py_lines.append(f"{get_indent()}    break")
                indent_level = max(0, indent_level - 1)
                continue

            m_redim = re.match(r'^redim\s*\(\s*([a-zA-Z0-9_]+)\s*,\s*(.+?)\s*\)$', line, re.IGNORECASE)
            if not m_redim:
                m_redim = re.match(r'^redim\s*\(\s*([a-zA-Z0-9_]+)\s*\[\s*(.+?)\s*\]\s*\)$', line, re.IGNORECASE)
            if m_redim:
                arr_name = m_redim.group(1)
                new_size = clean_expr(m_redim.group(2))
                body_py_lines.append(f"{get_indent()}_redim({arr_name}, int({new_size}))")
                continue

            m_lire = re.match(r'^lire\s*\((.+)\)$', line, re.IGNORECASE)
            if m_lire:
                vars_to_read = [v.strip() for v in m_lire.group(1).split(',')]
                for v in vars_to_read:
                    body_py_lines.append(f"{get_indent()}{v} = _lire_safe()")
                continue

            m = re.match(r'^(?:[eé]crire|afficher)\s*\((.*?)\)$', line, re.IGNORECASE)
            if m:
                expr = m.group(1)
                if re.match(r'^lire\s*\(', next_line, re.IGNORECASE):
                    body_py_lines.append(f"{get_indent()}print({clean_expr(expr)}, end='')")
                else:
                    body_py_lines.append(f"{get_indent()}print({clean_expr(expr)})")
                continue
            
            m_inc = re.match(r'^([a-zA-Z0-9_\[\]]+)\s*\+\+$', line)
            if m_inc:
                body_py_lines.append(f"{get_indent()}{m_inc.group(1)} += 1")
                continue

            m_dec = re.match(r'^([a-zA-Z0-9_\[\]]+)\s*--$', line)
            if m_dec:
                body_py_lines.append(f"{get_indent()}{m_dec.group(1)} -= 1")
                continue

            if re.search(r'<-+', line):
                parts = re.split(r'<-+', line, maxsplit=1)
                body_py_lines.append(f"{get_indent()}{parts[0].strip()} = {clean_expr(parts[1].strip())}")
                continue

            line = re.sub(r'(?i)\bajouter\s*\(\s*([^,]+)\s*,\s*(.+?)\s*\)', r'\1.append(\2)', line)
            line = re.sub(r'(?i)\b([a-zA-Z0-9_]+)\.ajouter\s*\(\s*(.+?)\s*\)', r'\1.append(\2)', line)

            body_py_lines.append(f"{get_indent()}{clean_expr(line)}")

    py_lines.extend(l for l in body_py_lines if l is not None)
    return "\n".join(py_lines)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python run_algo.py <fichier.algo>")
        sys.exit(1)

    algo_file = sys.argv[1]
    py_code = transpile_to_python(algo_file)
    
    temp_file = os.path.join(os.path.dirname(algo_file), "temp_exec.py")
    
    with open(temp_file, 'w', encoding='utf-8') as f:
        f.write(py_code)

    try:
        subprocess.run([sys.executable, temp_file])
    except Exception as e:
        print(f"\nErreur d'exécution: {e}")
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)