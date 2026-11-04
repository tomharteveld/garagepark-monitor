"""One-time login for the Business Profile API. Run in Cloud Shell (or anywhere) next to client.json:
    python3 gbp_oauth.py
Prints the JSON for the GitHub secret GBP_OAUTH_JSON. Needs no extra packages."""
import glob, json, os, urllib.parse, urllib.request
files = [f for f in ['client.json'] + sorted(glob.glob('client_secret*.json'), key=os.path.getmtime, reverse=True) if os.path.exists(f)]
if not files:
    raise SystemExit('Geen client.json of client_secret_....json in deze map. Upload het gedownloade OAuth-bestand via (drie puntjes) > Upload en probeer opnieuw.')
print('OAuth-client uit ' + files[0])
c = json.load(open(files[0]))
c = c.get('installed') or c.get('web') or c
redirect = 'http://localhost:8085/'
url = 'https://accounts.google.com/o/oauth2/auth?' + urllib.parse.urlencode(dict(
    client_id=c['client_id'], redirect_uri=redirect, response_type='code', access_type='offline', prompt='consent',
    scope='https://www.googleapis.com/auth/business.manage'))
print('\n1. Open deze link in een incognitovenster en log in met seodata@folloagency.com:\n\n' + url + '\n')
print('2. Na Toestaan krijg je een pagina die niet laadt (localhost). Dat klopt.')
pasted = input('3. Kopieer de volledige adresbalk van die pagina en plak hem hier: ').strip()
code = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).get('code', [pasted])[0]
data = urllib.parse.urlencode(dict(code=code, client_id=c['client_id'], client_secret=c['client_secret'],
                                   redirect_uri=redirect, grant_type='authorization_code')).encode()
tok = json.load(urllib.request.urlopen('https://oauth2.googleapis.com/token', data))
if 'refresh_token' not in tok:
    raise SystemExit('Geen refresh token ontvangen: ' + json.dumps(tok))
out = dict(type='authorized_user', client_id=c['client_id'], client_secret=c['client_secret'], refresh_token=tok['refresh_token'])
print('\nGelukt. Plak alles tussen de lijnen in het GitHub secret GBP_OAUTH_JSON:\n' + '=' * 60 + '\n' + json.dumps(out) + '\n' + '=' * 60)
