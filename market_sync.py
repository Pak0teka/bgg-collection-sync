"""Public offers only. Per-source stale fallback, bounded requests, no private data output."""
import json, re, time, os, hashlib, unicodedata
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from html import unescape
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from datetime import datetime, timezone

CATALOG = [
 ('draco','Dracotienda','https://dracotienda.com/1879-ofertas','Primeras 3 páginas de ofertas de juegos.'),
 ('tone','T0n3 Chollos','https://www.t0n3chollos.es/categoria/juegos-de-mesa','Listado público de juegos de mesa; disponibilidad no confirmada.'),
 ('busca','BuscaJuegos','https://buscajuegos.es/','Consulta externa; importación pendiente de verificar.'),
 ('radar','Radar Wargames','https://radarwargames.com/','Consulta externa; importación pendiente de verificar.'),
 ('ofertasjdm','Ofertas Juegos de Mesa','https://www.ofertasjuegosdemesa.com/','Ofertas del escaparate público; puede incluir packs y accesorios.'),
 ('dados','TirandoDados','https://www.tirandodados.es/comparador','Selección pública de destacados, ranking y catálogo; no todo el catálogo.'),
 ('cubos','MueveCubos','https://muevecubos.com/','Consulta externa; importación pendiente de verificar.'),
 ('sheep','NeedSheep','https://needsheep.com/ofertas','Las 100 ofertas del listado público; referencias de precio indicadas por el comparador.'),
 ('chollo','CholloMeeples','https://www.chollomeeples.com/','Consulta externa; importación pendiente de verificar.'),
 ('ludonauta','Ludonauta','https://www.ludonauta.es/','Consulta externa; importación pendiente de verificar.'),
 ('ludoku','Ludoku','https://www.ludoku.com/','Consulta externa: asociaciones juego–producto pendientes de verificar.'),
]
def now(): return datetime.now(timezone.utc).isoformat()
def number(v):
 try: return float(v)
 except (TypeError,ValueError): return None
def money(v):
 m=re.search(r'\d[\d.,]*',v or '')
 if not m: return None
 s=m.group(0)
 if ',' in s: s=s.replace('.','').replace(',','.')
 return number(s)
def key(s): return re.sub(r'[^a-z0-9]+',' ',unicodedata.normalize('NFKD',s or '').encode('ascii','ignore').decode().lower()).strip()
def http(s):
 try:
  p=urlparse(s or '')
  return s if p.scheme=='https' and p.hostname and '.' in p.hostname and not p.username else None
 except ValueError: return None
class Node:
 def __init__(self,tag='',attrs=None): self.tag,self.attrs,self.children=tag,dict(attrs or []),[]
 def text(self): return ' '.join(x.text() if isinstance(x,Node) else x for x in self.children).strip()
 def all(self,cls=None,tag=None):
  found=[]
  for x in self.children:
   if not isinstance(x,Node): continue
   if (not cls or cls in x.attrs.get('class','').split()) and (not tag or tag==x.tag): found.append(x)
   found.extend(x.all(cls,tag))
  return found
 def one(self,cls=None,tag=None): return next(iter(self.all(cls,tag)),Node())
class DOM(HTMLParser):
 def __init__(self,html):
  super().__init__(convert_charrefs=True);self.root=Node();self.stack=[self.root];self.feed(html)
 def handle_starttag(self,t,a):
  n=Node(t,a);self.stack[-1].children.append(n)
  if t not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'): self.stack.append(n)
 def handle_endtag(self,t):
  for i in range(len(self.stack)-1,0,-1):
   if self.stack[i].tag==t: self.stack=self.stack[:i];break
 def handle_data(self,d): self.stack[-1].children.append(d)
def base(source,name,url,price):
 return dict(id=source+'_'+hashlib.sha256((url or name).encode()).hexdigest()[:20],sourceId=source,name=name,url=url,price=price,referencePrice=None,shipping=None,language=None,edition=None,ean=None,availability='unknown',stockLabel='Disponibilidad sin confirmar',checked=now(),store='Tienda no indicada',bggId=None,image=None)
def parse_dados(html):
 m=re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',html,re.S)
 if not m: raise ValueError('No public data')
 d=json.loads(m.group(1))['props']['pageProps']['initialData'];out={}
 for g in sum([d.get(k,[]) for k in ('featuredDeals','topRankedInStock','games')],[]):
  c=g.get('commercial') or {};url=http(c.get('url'));p=base('dados',g['name'],url,number(c.get('bestPrice')))
  p.update(store=c.get('storeName') or 'Tienda no indicada',referencePrice=number(c.get('originalPrice')),shipping=number(c.get('shippingCost')),checked=c.get('lastScrapedAt') or now(),availability='in_stock' if c.get('inStock') is True else 'out_of_stock' if c.get('inStock') is False else 'unknown',stockLabel='Disponible según comparador' if c.get('inStock') is True else 'Disponibilidad por confirmar',bggId=int(g['id']),image=g.get('image') or g.get('thumbnail'),sourceURL='https://www.tirandodados.es/comparador')
  if url: out[url]=p
 return list(out.values())
def parse_sheep(html):
 out=[]
 for n in DOM(html).root.all('gc-row'):
  a=n.one('gc-cta-btn').attrs.get('href','');url=http(parse_qs(urlparse(a).query).get('url',[None])[0]);p=base('sheep',n.one('gc-row-name').one(tag='a').text(),url,number(n.attrs.get('data-sort-price')))
  shipping=n.one('gc-row-shipping').text();img=n.one('gc-row-thumb').one(tag='img').attrs
  p.update(store=n.one('gc-row-store').text(),referencePrice=money(n.one('gc-row-prev').text()),image=urljoin('https://needsheep.com',img.get('src','')),sourceURL=urljoin('https://needsheep.com',n.one('gc-row-name').one(tag='a').attrs.get('href','')),shipping=0 if 'Envío gratis' in shipping else money(shipping),language='es' if '🇪🇸' in n.text() else 'en' if '🇬🇧' in n.text() else None)
  # Availability is not separately supplied in this list; do not infer stock from a price.
  if url: out.append(p)
 return out
def parse_tone(html):
 out=[]
 for n in DOM(html).root.all('deal-card'):
  a=n.attrs;url=http(n.one('offer-link').attrs.get('href'));p=base('tone',a.get('data-title') or n.one('title-link').text(),url,number(a.get('data-price')))
  p.update(store=a.get('data-store') or 'Tienda no indicada',referencePrice=money(n.one('old').text()),image=urljoin('https://www.t0n3chollos.es',n.one(tag='img').attrs.get('src','')),sourceURL=urljoin('https://www.t0n3chollos.es',n.one('title-link').attrs.get('href','')),listedAt=a.get('data-date'))
  if 'is-out' in a.get('class','').split(): p.update(availability='out_of_stock',stockLabel='Agotada según fuente')
  if 'is-stale' in a.get('class','').split(): p['stockLabel']='La fuente marca esta oferta como antigua; comprueba vigencia'
  if url: out.append(p)
 return out
def parse_ojm(html):
 out=[]
 for n in DOM(html).root.all('game-card'):
  url=http(n.one('game-link').attrs.get('href'));p=base('ofertasjdm',n.one('game-name').text(),url,money(n.one('price-current').text()));stock=n.one('game-stock')
  p.update(store=n.one('game-shop').text(),referencePrice=money(n.one('price-original').text()),image=urljoin('https://www.ofertasjuegosdemesa.com',n.one('game-image').attrs.get('src','')),sourceURL='https://www.ofertasjuegosdemesa.com/',availability=stock.attrs.get('data-stock','unknown'),stockLabel=stock.text() or 'Disponibilidad sin confirmar')
  if url: out.append(p)
 return out
def parse_game(item):
 value=lambda path:number(item.find(path).get('value')) if item.find(path) is not None else None
 links=lambda t:[dict(id=int(x.get('id')),name=x.get('value')) for x in item.findall('link') if x.get('type')==t]
 ranks=item.findall('statistics/ratings/ranks/rank');r=next((number(x.get('value')) for x in ranks if x.get('name')=='boardgame'),None)
 votes=[]
 for result in item.findall("poll[@name='suggested_numplayers']/results"):
  counts={x.get('value'):int(x.get('numvotes','0')) for x in result.findall('result')};votes.append(dict(players=result.get('numplayers'),best=counts.get('Best',0),recommended=counts.get('Recommended',0),notRecommended=counts.get('Not Recommended',0),total=sum(counts.values())))
 names=[x.get('value') for x in item.findall('name')];name=next((x.get('value') for x in item.findall('name') if x.get('type')=='primary'),names[0]);bases=[dict(id=int(x.get('id')),name=x.get('value')) for x in item.findall('link') if x.get('type')=='boardgameexpansion' and x.get('inbound')=='true']
 return dict(id=int(item.get('id')),name=name,names=names,rank=r,rating=value('statistics/ratings/average'),weight=value('statistics/ratings/averageweight'),community=votes,image=item.findtext('image'),updated=now(),public=dict(average=value('statistics/ratings/average'),minplayers=value('minplayers'),maxplayers=value('maxplayers')),bgg_details=dict(schema=1,type=item.get('type'),complexity=value('statistics/ratings/averageweight'),base_games=bases,categories=links('boardgamecategory'),mechanics=links('boardgamemechanic')))
def enrich(items,old,fetch):
 token=os.environ.get('BGG_TOKEN');cache=old.get('games',{}).copy();lookup=old.get('lookup',{}).copy();aliases={}
 try:
  collection=json.load(open('collection.json',encoding='utf-8'))['games']
 except (OSError,ValueError,KeyError): collection=[]
 for g in collection+list(cache.values()):
  for name in [g['name']]+g.get('names',[]): aliases.setdefault(key(name),set()).add(int(g['id']))
 queries=0
 for p in items:
  k=key(p['name']);hits=aliases.get(k,set());found=lookup.get(k)
  if p.get('bggId'): p['matchMethod']='source';continue
  if len(hits)==1: p['bggId']=next(iter(hits));p['matchMethod']='name';continue
  if found and time.time()-found.get('time',0)<2592000:
   p['bggId']=found.get('id');p['matchMethod']='name' if p['bggId'] else None;continue
  if not token or queries>=30: continue
  try:
   time.sleep(5.2);queries+=1;root=ET.fromstring(fetch('https://boardgamegeek.com/xmlapi2/search?'+urlencode(dict(query=p['name'],type='boardgame,boardgameexpansion',exact=1)),token));hits={int(i.get('id')) for i in root.findall('item') if any(key(n.get('value'))==k for n in i.findall('name'))};gid=next(iter(hits)) if len(hits)==1 else None;lookup[k]=dict(id=gid,time=time.time());p['bggId']=gid;p['matchMethod']='name' if gid else None
  except Exception: continue
 ids=sorted({p['bggId'] for p in items if p.get('bggId') and (str(p['bggId']) not in cache or time.time()-datetime.fromisoformat(cache[str(p['bggId'])]['updated']).timestamp()>604800)})[:240]
 warning=None
 for offset in range(0,len(ids),20):
  if not token: warning='Datos BGG pendientes de actualizar.';break
  try:
   time.sleep(5.2);root=ET.fromstring(fetch('https://boardgamegeek.com/xmlapi2/thing?id='+','.join(map(str,ids[offset:offset+20]))+'&stats=1',token))
   for item in root.findall('item'): cache[item.get('id')]=parse_game(item)
  except Exception: warning='Algunas fichas BGG no se pudieron actualizar; se conservan las anteriores.'
 # A linked BGG ID is evidence from the source, not proof of edition or language.
 return cache,lookup,warning
def update_market(data,fetch):
 old=data.get('market',{});previous=old.get('sources',{});sources={};items=[]
 adapters={'dados':parse_dados,'sheep':parse_sheep,'tone':parse_tone,'ofertasjdm':parse_ojm}
 for sid,name,url,coverage in CATALOG:
  info=dict(id=sid,name=name,url=url,coverage=coverage,checked=now(),updated=None,status='external',warning=None,items=[])
  try:
   if sid=='draco':
    if data.get('offers',{}).get('warning'): raise ValueError('Draco stale')
    rows=[dict(p,sourceId='draco',checked=data['offers']['updated'],shipping=None,language=None,edition=None,ean=None,bggId=None,sourceURL=url) for p in data['offers']['items']]
   elif sid in adapters: rows=adapters[sid](fetch(url))
   else: sources[sid]=info;continue
   rows=[p for p in rows if p.get('name') and http(p.get('url')) and isinstance(p.get('price'),(int,float)) and p['price']>0]
   if not rows: raise ValueError('Empty source or changed format')
   info.update(status='imported',updated=now(),items=rows);print(sid+': '+str(len(rows))+' offers')
  except Exception as e:
   info.update(status='error',warning='No se pudo actualizar; se conserva la última copia disponible.',updated=previous.get(sid,{}).get('updated'),items=previous.get(sid,{}).get('items',[]));print(sid+': retaining cache ('+type(e).__name__+')')
  sources[sid]=info;items.extend(info['items'])
 games,lookup,warning=enrich(items,old,fetch)
 history=old.get('history',{}).copy()
 for p in items:
  if sources[p['sourceId']]['status']!='imported': continue
  points=history.setdefault(p['id'],[]);stamp=p['checked'][:10];point=dict(date=stamp,price=p['price'],shipping=p.get('shipping'))
  if points and points[-1]['date']==stamp: points[-1]=point
  else: points.append(point)
  history[p['id']]=points[-180:]
 data['market']=dict(schema=1,checked=now(),sources=sources,items=items,games=games,lookup=lookup,history=history,bggWarning=warning)
 return data
