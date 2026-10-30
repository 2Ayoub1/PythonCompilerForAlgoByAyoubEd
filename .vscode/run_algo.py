import sys
import os
import re
import subprocess

def transpile_to_python(source_path):
    with open(source_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    py_lines = []
    
    # Injection de la fonction pour gérer la saisie avec support des espaces
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

    indent_level = 0
    in_variables = False
    in_debut = False
    declared_vars = set()

    def get_indent():
        return "    " * indent_level

    # Nettoyage des expressions (maths, booléens, et opérateurs)
    def clean_expr(expr):
        e = re.sub(r'\bmod\b', '%', expr, flags=re.IGNORECASE)
        e = re.sub(r'\bdiv\b', '//', e, flags=re.IGNORECASE)
        e = re.sub(r'\bvrai\b', 'True', e, flags=re.IGNORECASE)
        e = re.sub(r'\bfaux\b', 'False', e, flags=re.IGNORECASE)
        e = re.sub(r'\bet\b', 'and', e, flags=re.IGNORECASE)
        e = re.sub(r'\bou\b', 'or', e, flags=re.IGNORECASE)
        e = re.sub(r'\bnon\b', 'not', e, flags=re.IGNORECASE)
        return e

    def clean_cond(cond):
        c = cond.strip()
        if c.endswith(':'):
            c = c[:-1].strip()
        c = c.replace('<=', '§LE§').replace('>=', '§GE§').replace('<>', '§NE§').replace('!=', '§NE§')
        c = re.sub(r'(?<![<>!])=(?!=)', '==', c)
        c = c.replace('§LE§', '<=').replace('§GE§', '>=').replace('§NE§', '!=')
        return clean_expr(c)

    # Première passe rapide pour détecter les variables utilisées/assignées et les déclarations
    body_py_lines = []

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        if '//' in line:
            line = line.split('//')[0].strip()
        if not line:
            continue
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
        if re.match(r'^(?:variables?|var)\s*:?$', lower_line):
            in_variables = True
            continue
        
        # Flexibilité: debut ou début
        if re.match(r'^d[eé]but$', lower_line):
            in_variables = False
            in_debut = True
            # S'assurer que r et temp sont initialisés pour éviter le NameError
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
                
                if re.match(r'^bool[eé]en$|^bool$|^boolean$', var_type):
                    default_val = "False"
                else:
                    default_val = "0"
                    
                # Support de <-, <--, <--- dans les variables (ex: x <- 5 : Entier)
                vars_raw = re.sub(r'<-+\s*[^,]+', '', left_side)
                var_list = [v.strip() for v in vars_raw.split(',') if v.strip()]
                
                for v in var_list:
                    clean_v_name = v[:v.find('[')].strip() if '[' in v else v
                    declared_vars.add(clean_v_name)
                    if '[' in v and v.strip().endswith(']'):
                        # Tableaux
                        dims = [d.strip() for d in re.findall(r'\[(.*?)\]', v[v.find('['):])]
                        if len(dims) == 1:
                            d1 = dims[0] if dims[0] else "0"
                            body_py_lines.append(f"{clean_v_name} = [0] * ({d1})")
                        elif len(dims) == 2:
                            d1 = dims[0] if dims[0] else "0"
                            d2 = dims[1] if dims[1] else "0"
                            body_py_lines.append(f"{clean_v_name} = [[0] * ({d2}) for _ in range({d1})]")
                        else:
                            body_py_lines.append(f"{clean_v_name} = []")
                    else:
                        body_py_lines.append(f"{clean_v_name} = {default_val}")
            continue

        if in_debut:
            # Flexibilité fins de blocs
            if re.match(r'^fin\s*_?\s*(pour|si|tant\s*que)$', lower_line):
                indent_level = max(0, indent_level - 1)
                continue

            if lower_line in ['sortir', 'quitter', 'interrompre']:
                body_py_lines.append(f"{get_indent()}break")
                continue

            # Sinon Si
            m = re.match(r'^sinon\s+si\s*\((.+?)\)(?:\s+alors)?$', line, re.IGNORECASE)
            if not m:
                m = re.match(r'^sinon\s+si\s+(.+?)\s+alors$', line, re.IGNORECASE)
            if m:
                indent_level = max(0, indent_level - 1)
                body_py_lines.append(f"{get_indent()}elif {clean_cond(m.group(1))}:")
                indent_level += 1
                continue

            # Sinon
            if lower_line == 'sinon':
                indent_level = max(0, indent_level - 1)
                body_py_lines.append(f"{get_indent()}else:")
                indent_level += 1
                continue

            # Si
            m = re.match(r'^si\s*\((.+?)\)(?:\s+alors)?$', line, re.IGNORECASE)
            if not m:
                m = re.match(r'^si\s+(.+?)\s+alors$', line, re.IGNORECASE)
            if m:
                body_py_lines.append(f"{get_indent()}if {clean_cond(m.group(1))}:")
                indent_level += 1
                continue

            # Boucle Pour (Support de <-, <--, etc.)
            m = re.match(r'^(?:pour\s+)?([a-zA-Z0-9_]+)\s*(?:<-+|=)\s*(.+?)\s+(?:a|à)\s+(.+?)(?:\s+faire)?$', line, re.IGNORECASE)
            if m:
                var, start, end = m.groups()
                body_py_lines.append(f"{get_indent()}for {var} in range(int({start}), int({end}) + 1):")
                indent_level += 1
                continue

            # Boucle TantQue
            m = re.match(r'^(?:tant\s*que|pendant\s*que)\s+(.+?)(?:\s+faire)?$', line, re.IGNORECASE)
            if m:
                body_py_lines.append(f"{get_indent()}while {clean_cond(m.group(1))}:")
                indent_level += 1
                continue

            # Boucle Répéter
            if re.match(r'^r[eé]p[eé]ter$', lower_line):
                body_py_lines.append(f"{get_indent()}while True:")
                indent_level += 1
                continue

            # Boucle Jusqu'à
            m = re.match(r'^jusqu[\'\s]*[aà]\s*\((.+?)\)$', line, re.IGNORECASE)
            if m:
                body_py_lines.append(f"{get_indent()}if {clean_cond(m.group(1))}:")
                body_py_lines.append(f"{get_indent()}    break")
                indent_level = max(0, indent_level - 1)
                continue

            # Lire
            m_lire = re.match(r'^lire\s*\((.+)\)$', line, re.IGNORECASE)
            if m_lire:
                vars_to_read = [v.strip() for v in m_lire.group(1).split(',')]
                for v in vars_to_read:
                    body_py_lines.append(f"{get_indent()}{v} = _lire_safe()")
                continue

            # Ecrire
            m = re.match(r'^(?:[eé]crire|afficher)\s*\((.*?)\)$', line, re.IGNORECASE)
            if m:
                expr = m.group(1)
                if re.match(r'^lire\s*\(', next_line, re.IGNORECASE):
                    body_py_lines.append(f"{get_indent()}print({clean_expr(expr)}, end='')")
                else:
                    body_py_lines.append(f"{get_indent()}print({clean_expr(expr)})")
                continue

            # Affectations (Support de <-, <--, <---, etc.)
            if re.search(r'<-+', line):
                parts = re.split(r'<-+', line, maxsplit=1)
                body_py_lines.append(f"{get_indent()}{parts[0].strip()} = {clean_expr(parts[1].strip())}")
                continue

            # Ajouter
            line = re.sub(r'(?i)\bajouter\s*\(\s*([^,]+)\s*,\s*(.+?)\s*\)', r'\1.append(\2)', line)
            line = re.sub(r'(?i)\b([a-zA-Z0-9_]+)\.ajouter\s*\(\s*(.+?)\s*\)', r'\1.append(\2)', line)

            body_py_lines.append(f"{get_indent()}{clean_expr(line)}")

    py_lines.extend(body_py_lines)
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