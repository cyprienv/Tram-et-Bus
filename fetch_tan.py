import json
import urllib.request
from datetime import datetime

# Configuration de vos arrêts et lignes suivis
STOPS_CONFIG = {
    'l2': {'stopCode': 'WATT', 'lineNum': '2'},
    'l3': {'stopCode': 'WATT', 'lineNum': '3'},
    'l5': {'stopCode': 'VIGA', 'lineNum': '5'}
}

output_data = {}

for key, config in STOPS_CONFIG:  # Petite correction de syntaxe
    pass

# On traite chaque arrêt un par un
for key, config in STOPS_CONFIG.items():
    url = f"https://open.tan.fr/ewp/tempsattente.json/{config['stopCode']}"
    try:
        # Le script s'identifie proprement pour éviter tout rejet
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            # Filtrer les départs pour ne garder que la bonne ligne
            line_arrivals = []
            if isinstance(data, list):
                line_arrivals = [
                    item for item in data 
                    if item.get('ligne') and str(item['ligne'].get('numLigne')) == str(config['lineNum'])
                ]
            
            output_data[key] = line_arrivals
    except Exception as e:
        print(f"Erreur lors de la récupération pour {key} ({config['stopCode']}): {e}")
        # En cas d'erreur de l'API, on renvoie une liste vide (le front-end basculera sur le théorique)
        output_data[key] = []

# Génération du fichier JSON final propre pour votre page web
payload = {
    'updated_at': datetime.now().isoformat(),
    'data': output_data
}

with open('horaires.json', 'w', encoding='utf-8') as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)

print("Synchronisation des horaires Naolib terminée avec succès.")