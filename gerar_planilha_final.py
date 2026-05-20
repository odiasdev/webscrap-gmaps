import json
import csv

with open('data/qualified_leads_cleaned.json', encoding='utf-8') as f:
    data = json.load(f)

# Só leads com pitch e ordenados por score
prontos = [d for d in data if d.get('pitch')]
prontos.sort(key=lambda x: x['qualification']['score'], reverse=True)

with open('data/leads_para_atacar.csv', 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f, delimiter=';')
    w.writerow([
        'Prioridade', 'Score', 'Tier', 'Nome', 'Telefone', 'Bairro',
        'Reviews', 'Rating', 'Dor', 'Mensagem',
        'Status', 'Data Envio', 'Resposta', 'Proxima Acao', 'Notas'
    ])
    for i, d in enumerate(prontos, 1):
        bairro = ''
        if ' - ' in d.get('address', ''):
            try:
                bairro = d['address'].split(' - ')[1].split(',')[0]
            except:
                bairro = ''
        w.writerow([
            i,
            d['qualification']['score'],
            d['qualification']['tier'],
            d['name'],
            d['phone'],
            bairro,
            d.get('reviews_count', ''),
            d.get('rating', ''),
            d['qualification']['dor_principal'],
            d['pitch']['mensagem_completa'],
            'frio',  # status inicial
            '', '', '', ''
        ])

print(f'Planilha gerada com {len(prontos)} leads em data/leads_para_atacar.csv')
print()
print('Distribuicao por tier:')
from collections import Counter
tiers = Counter(d['qualification']['tier'] for d in prontos)
for tier, count in sorted(tiers.items(), key=lambda x: -x[1]):
    print(f'  {tier}: {count}')