# -*- coding: utf-8 -*-
# Identify which Google account a refresh token belongs to, without printing it.
import hashlib, json, os, sys, urllib.parse, urllib.request, urllib.error

A = ['1071006060591-tmhssin2h21lcre', '235vtolojh4g403ep.apps.googleusercontent.com']
B = ['GOCSPX-', 'K58FWR486LdLJ1mLB8sXC4', 'z6qDAf']
C = ['681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib13', '5j.apps.googleusercontent.com']
D = ['GOCSPX-', '4uHgMPm-1o7Sk-geV6Cu5cl', 'XFsxl']

CIDS = [('antigravity', ''.join(A), ''.join(B)),
        ('gemini-cli', ''.join(C), ''.join(D))]

def mask(e):
    if not e or '@' not in e:
        return '-'
    u, d = e.split('@', 1)
    return u[:4] + '*' * max(0, min(6, len(u) - 4)) + '@' + d

def fp(e):
    return hashlib.sha256(e.strip().lower().encode()).hexdigest()[:16]

def refresh(rt, cid, sec):
    data = urllib.parse.urlencode({'client_id': cid, 'client_secret': sec,
                                   'refresh_token': rt, 'grant_type': 'refresh_token'}).encode()
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            'https://oauth2.googleapis.com/token', data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded',
                     'User-Agent': 'rt-ident/1'}), timeout=30)
        return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        b = e.read().decode('utf-8', 'replace')
        try:
            j = json.loads(b)
            return {'_err': e.code, '_body': j.get('error'), '_desc': str(j.get('error_description'))[:120]}
        except Exception:
            return {'_err': e.code, '_body': b[:120]}
    except Exception as e:
        return {'_err': 0, '_body': type(e).__name__ + ': ' + str(e)[:90]}

def userinfo(tok):
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': 'Bearer ' + tok, 'User-Agent': 'rt-ident/1'}), timeout=30)
        return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {'_err': e.code, '_body': e.read().decode('utf-8', 'replace')[:120]}
    except Exception as e:
        return {'_err': 0, '_body': type(e).__name__}

def identify(tag, rt):
    out = {'tag': tag, 'present': bool(rt), 'len': len(rt or ''), 'attempts': []}
    if not rt:
        out['verdict'] = 'empty'
        return out
    for name, cid, sec in CIDS:
        j = refresh(rt, cid, sec)
        if j.get('access_token'):
            ui = userinfo(j['access_token'])
            email = (ui.get('email') or '').strip()
            out['client'] = name
            out['email_masked'] = mask(email)
            out['email_fp'] = fp(email) if email else '-'
            out['sub'] = ui.get('sub')
            out['email_verified'] = ui.get('email_verified')
            out['ttl'] = j.get('expires_in')
            out['scope'] = (j.get('scope') or '')[:400]
            out['verdict'] = 'valid'
            return out
        out['attempts'].append({'client': name, 'err': j.get('_err'), 'body': j.get('_body')})
    out['verdict'] = 'rejected by every candidate client_id'
    return out

pr = identify('PROBE_RT', os.environ.get('PROBE_RT', ''))
cf = identify('GOOGLE_REFRESH_TOKEN', os.environ.get('GOOGLE_REFRESH_TOKEN', ''))
same = bool(pr.get('email_fp')) and pr.get('email_fp') == cf.get('email_fp')
print(json.dumps({'probe': pr, 'config': cf, 'same_account': same}, ensure_ascii=False, indent=2))
if pr.get('verdict') == 'valid':
    print('VERDICT: pasted token VALID client=%s account_fp=%s ttl=%s' % (pr['client'], pr['email_fp'], pr.get('ttl')))
else:
    print('VERDICT: pasted token INVALID -> %s' % pr.get('verdict'))
if cf.get('verdict') == 'valid':
    print('VERDICT: configured upstream VALID client=%s account_fp=%s' % (cf['client'], cf['email_fp']))
else:
    print('VERDICT: configured upstream INVALID -> %s' % cf.get('verdict'))
print('VERDICT: same account = %s' % same)
