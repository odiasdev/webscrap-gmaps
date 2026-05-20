import json

with open('data/qualified_leads_cleaned.json', encoding='utf-8') as f:
    data = json.load(f)

prontos = [d for d in data if d.get('pitch')]
prontos.sort(key=lambda x: x['qualification']['score'], reverse=True)

for d in prontos[:5]:
    print(f'--- {d["name"]} ---')
    print(d['pitch']['mensagem_completa'])
    print()