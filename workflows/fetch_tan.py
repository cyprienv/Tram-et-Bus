import json
import urllib.request
from datetime import datetime

# Configuration de tes arrêts (Wattignies et Vincent Gâche)
STOPS_CONFIG = {
    'l2': {'stopCode': 'WATT', 'lineNum': '2'},
    'l3': {'stopCode': 'WATT', 'lineNum': '3'},
    'l5': {'stopCode': 'VIGA', 'lineNum': '5'}
}

output_data = {}

for key, config in STOPS_CONFIG.items():
    # Tu peux remplacer ici par l'URL SIRI-Lite ou l'API Naolib officielle
    url = f"https://open.tan.fr/ewp/tempsattente.json/{config['stopCode']}"
    
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            if isinstance(data, list):
                line_arrivals = [
                    item for item in data 
                    if item.get('ligne') and str(item['ligne'].get('numLigne')) == str(config['lineNum'])
                ]
                output_data[key] = line_arrivals
            else:
                output_data[key] = []
    except Exception as e:
        print(f"Erreur pour {key}: {e}")
        output_data[key] = []

payload = {
    'updated_at': datetime.now().isoformat(),
    'data': output_data
}

with open('horaires.json', 'w', encoding='utf-8') as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)

print("Mise à jour SIRI-Lite / temps réel réussie.")
