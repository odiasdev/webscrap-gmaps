import json

with open('data/qualified_leads_cleaned.json', encoding='utf-8') as f:
    data = json.load(f)

nova_msg = "Oi, sou Dias, fundador do Arena App. Vi que a Orion tem 174 avaliações no Google — volume alto pra gerenciar tudo manual. Sem sistema de reservas, agendamento via WhatsApp vira gargalo na hora do pico. O Arena App é grátis pra começar, sem mensalidade obrigatória — posso passar aí terça ou quinta pra te mostrar como funciona?"

for d in data:
    if d['name'] == 'Quadra Orion Esporte Clube':
        d['pitch']['mensagem_completa'] = nova_msg
        d['pitch']['needs_review'] = False
        d['pitch']['review_reason'] = ''
        print('Atualizado:', d['name'])
        break

with open('data/qualified_leads_cleaned.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print('Salvo.')