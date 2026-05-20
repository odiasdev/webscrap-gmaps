import json

with open('data/qualified_leads_cleaned.json', encoding='utf-8') as f:
    data = json.load(f)

# Filtra só leads que têm pitch (tier frio fica de fora)
com_pitch = [d for d in data if d.get('pitch')]
sem_pitch = [d for d in data if not d.get('pitch')]

flagged = [d for d in com_pitch if d['pitch'].get('needs_review')]

print(f'Total: {len(data)}')
print(f'Com pitch: {len(com_pitch)}')
print(f'Sem pitch (tier frio): {len(sem_pitch)}')
print(f'Flaggeds pra revisao: {len(flagged)}')
print()

for d in flagged[:10]:
    print(f'--- {d["name"]} | {d["phone"]} ---')
    print(f'Razao: {d["pitch"].get("review_reason", "")}')
    print(f'Msg: {d["pitch"]["mensagem_completa"]}')
    print()