from pathlib import Path
import csv, json
SEED = 20260816
with Path('input/data/values.csv').open(encoding='utf-8-sig') as f:
    values = [float(row['value']) for row in csv.DictReader(f)]
result = {'mean': sum(values)/len(values), 'count': len(values), 'seed': SEED}
Path('results').mkdir(exist_ok=True)
Path('results/summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
