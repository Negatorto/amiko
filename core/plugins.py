import os
import json
import importlib.util
from markupsafe import Markup

from core.config import MODULES_DIR

def load_modules(app):
    loaded_modules = []
    
    if not os.path.exists(MODULES_DIR):
        os.makedirs(MODULES_DIR)
        return loaded_modules
        
    for mod_name in os.listdir(MODULES_DIR):
        mod_path = os.path.join(MODULES_DIR, mod_name)
        if not os.path.isdir(mod_path):
            continue
            
        json_path = os.path.join(mod_path, 'module.json')
        if not os.path.exists(json_path):
            continue
            
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                mod_data = json.load(f)
                
            if not mod_data.get('enabled', True):
                continue
                
            # Check if icon is an SVG and mark it safe for HTML rendering
            if 'icon' in mod_data and mod_data['icon'].startswith('<svg'):
                mod_data['icon'] = Markup(mod_data['icon'])

            template_html = ""
            tpl_path = os.path.join(mod_path, 'template.html')
            if os.path.exists(tpl_path):
                with open(tpl_path, 'r', encoding='utf-8') as f:
                    template_html = f.read()
                    
            mod_data['template_html'] = template_html
            mod_data['id'] = mod_name
            
            # Check for backend blueprint
            backend_path = os.path.join(mod_path, 'backend.py')
            if os.path.exists(backend_path):
                spec = importlib.util.spec_from_file_location(f"module_{mod_name}", backend_path)
                mod_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod_module)
                
                # Assume blueprint is named 'blueprint' inside backend.py
                if hasattr(mod_module, 'blueprint'):
                    app.register_blueprint(mod_module.blueprint)
                    print(f"[AMIKO Plugin System] Registered Backend Blueprint for: {mod_name}")
                    
            loaded_modules.append(mod_data)
            print(f"[AMIKO Plugin System] Successfully loaded module UI: {mod_name}")
            
        except Exception as e:
            print(f"[AMIKO Plugin System] Error loading module {mod_name}: {e}")
            
    # Sort modules by index, fallback to 999 if not specified
    loaded_modules.sort(key=lambda x: x.get('index', 999))
            
    return loaded_modules
