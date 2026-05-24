import urllib.request, json

r = urllib.request.urlopen('http://localhost:8000/api/latest')
data = json.loads(r.read())
run = data.get('run', {})
results = data.get('results', [])

print('=== Latest Run ===')
if run:
    print(f"  Run time:     {run.get('run_time')}")
    print(f"  Market trend: {run.get('market_trend')}")
    print(f"  VIX:          {run.get('vix_value')}")
    print(f"  NIFTY change: {run.get('nifty_change')}%")
    print(f"  Email sent:   {run.get('email_sent')}")
else:
    print('  No run data yet')

print(f"\nStocks analysed: {len(results)}")
buys  = [r for r in results if r.get('signal') == 'BUY']
sells = [r for r in results if r.get('signal') == 'SELL']
watch = [r for r in results if r.get('signal') == 'WATCH']
print(f"BUY={len(buys)}  SELL={len(sells)}  WATCH={len(watch)}")

print("\nTop 5 by score:")
for r in results[:5]:
    print(f"  {r.get('symbol'):>12}  Rs.{r.get('price'):>10}  score={r.get('score')}  {r.get('signal')}  chg={r.get('intraday_change')}%  co={r.get('company_name','')}")
