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
    url = f"https://open.tan.fr/ewp/tempsattente.json/{config['stopCode']}"
    
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            raw_data = response.read().decode('utf-8')
            data = json.loads(raw_data)
            
            print(f"Données brutes reçues pour {config['stopCode']} : {raw_data[:200]}")
            
            if isinstance(data, list):
                # Filtre plus souple qui cherche le numéro de ligne dans l'objet
                line_arrivals = []
                for item in data:
                    ligne_info = item.get('ligne', {})
                    num_ligne = str(ligne_info.get('numLigne', ''))
                    # On accepte si le numéro correspond ou si le texte contient le chiffre
                    if config['lineNum'] in num_ligne or config['lineNum'] == str(item.get('numLigne', '')):
                        line_arrivals.append(item)
                
                # Si le filtre strict ne donne rien, on prend tout ce que l'arrêt renvoie pour voir
                if not line_arrivals and data:
                    output_data[key] = data
                else:
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

print("Mise à jour des horaires Naolib réussie.")
