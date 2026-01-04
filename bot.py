import os
import json
import asyncio
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import requests
from bs4 import BeautifulSoup
import re

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# File per salvare i dati
DATA_FILE = 'vinted_data.json'

class VintedMonitor:
    def __init__(self):
        self.data = self.load_data()
        self.session = requests.Session()
        self._setup_session()
    
    def _setup_session(self):
        """Configura la sessione con headers realistici"""
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'it-IT,it;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
        })
    
    def load_data(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r') as f:
                    return json.load(f)
            except:
                return {'users': {}}
        return {'users': {}}
    
    def save_data(self):
        with open(DATA_FILE, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def add_user_link(self, user_id, link, name):
        user_id = str(user_id)
        if user_id not in self.data['users']:
            self.data['users'][user_id] = {'links': {}}
        
        link_id = str(len(self.data['users'][user_id]['links']) + 1)
        self.data['users'][user_id]['links'][link_id] = {
            'url': link,
            'name': name,
            'last_items': [],
            'added_at': datetime.now().isoformat()
        }
        self.save_data()
        return link_id
    
    def remove_user_link(self, user_id, link_id):
        user_id = str(user_id)
        if user_id in self.data['users'] and link_id in self.data['users'][user_id]['links']:
            del self.data['users'][user_id]['links'][link_id]
            self.save_data()
            return True
        return False
    
    def get_user_links(self, user_id):
        user_id = str(user_id)
        if user_id in self.data['users']:
            return self.data['users'][user_id]['links']
        return {}
    
    def extract_json_from_html(self, html):
        """Estrae dati JSON embedded nell'HTML"""
        items = []
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # Metodo 1: Cerca script tags con JSON
            scripts = soup.find_all('script')
            logger.info(f"📜 Trovati {len(scripts)} script tags")
            
            for idx, script in enumerate(scripts):
                if not script.string:
                    continue
                
                script_text = script.string.strip()
                
                # Cerca pattern comuni dove Vinted mette i dati
                patterns = [
                    r'window\.__INITIAL_STATE__\s*=\s*({.+?});',
                    r'window\.__PRELOADED_STATE__\s*=\s*({.+?});',
                    r'window\.__NUXT__\s*=\s*({.+?});',
                    r'window\.__data\s*=\s*({.+?});',
                    r'data:\s*({.+?"items":.+?})',
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, script_text, re.DOTALL)
                    if match:
                        try:
                            json_str = match.group(1)
                            # Pulisci il JSON
                            json_str = json_str.replace('\n', '').replace('\r', '')
                            
                            data = json.loads(json_str)
                            logger.info(f"✅ JSON trovato nel pattern: {pattern[:30]}")
                            
                            # Cerca items ricorsivamente
                            found_items = self._find_items_recursive(data)
                            if found_items:
                                return found_items
                        except:
                            continue
            
            # Metodo 2: Cerca nell'HTML diretto (grid items)
            logger.info("🔍 Provo metodo HTML diretto...")
            
            # Cerca tutti i link agli articoli
            all_links = soup.find_all('a', href=re.compile(r'/items/\d+'))
            logger.info(f"🔗 Trovati {len(all_links)} link articoli")
            
            seen_ids = set()
            
            for link in all_links[:30]:  # Limita a 30
                try:
                    # Estrai ID dall'href
                    href = link.get('href', '')
                    id_match = re.search(r'/items/(\d+)', href)
                    if not id_match:
                        continue
                    
                    item_id = id_match.group(1)
                    
                    # Skip duplicati
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                    
                    url = href if href.startswith('http') else f"https://www.vinted.it{href}"
                    
                    # Il link o il suo parent contengono le info
                    # Cerca nel link stesso e nei parent
                    search_area = link.find_parent(['div', 'article']) or link
                    
                    # Cerca titolo - spesso nel link stesso o in un div vicino
                    title = "Articolo"
                    
                    # Titolo potrebbe essere nell'attributo title del link
                    if link.get('title'):
                        title = link['title']
                    
                    # O nel testo del link
                    link_text = link.get_text(strip=True)
                    if link_text and len(link_text) > 3 and not link_text.isdigit():
                        title = link_text
                    
                    # O in un div con data-testid o class specifiche
                    if title == "Articolo" and search_area:
                        # Cerca tutti i div di testo
                        text_divs = search_area.find_all(['div', 'p', 'span', 'h2', 'h3'])
                        for div in text_divs:
                            text = div.get_text(strip=True)
                            # Ignora testi troppo corti o che sembrano prezzi
                            price_pattern = r'^[\d,\.\€\$\£\s]+ len(text) > 10 and not re.match(r'^[\d,\.€$£\s]+
                    
                except Exception as e:
                    continue
            
            if items:
                logger.info(f"✅ Estratti {len(items)} articoli dal HTML!")
                return items[:20]
            
            logger.warning("❌ Nessun articolo trovato")
            return []
            
        except Exception as e:
            logger.error(f"❌ Errore parsing: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def _find_items_recursive(self, obj, depth=0, max_depth=15):
        """Cerca array 'items' ricorsivamente"""
        if depth > max_depth:
            return None
        
        if isinstance(obj, dict):
            # Cerca chiave 'items'
            if 'items' in obj and isinstance(obj['items'], list) and len(obj['items']) > 0:
                first = obj['items'][0]
                if isinstance(first, dict) and 'id' in first:
                    logger.info(f"✅ Trovato array items a profondità {depth}")
                    return self._parse_items_array(obj['items'])
            
            # Cerca in tutte le chiavi
            for key, value in obj.items():
                result = self._find_items_recursive(value, depth + 1, max_depth)
                if result:
                    return result
        
        elif isinstance(obj, list):
            for item in obj:
                result = self._find_items_recursive(item, depth + 1, max_depth)
                if result:
                    return result
        
        return None
    
    def _parse_items_array(self, items_array):
        """Parsea array di items in formato standard"""
        parsed = []
        
        for item in items_array[:20]:
            try:
                if not isinstance(item, dict) or 'id' not in item:
                    continue
                
                # ID
                item_id = str(item['id'])
                
                # Titolo
                title = item.get('title', 'Senza titolo')
                
                # Prezzo
                price = str(item.get('price', '0'))
                currency = item.get('currency', '€')
                
                # URL
                url = item.get('url', f"https://www.vinted.it/items/{item_id}")
                if not url.startswith('http'):
                    url = 'https://www.vinted.it' + url
                
                # Foto
                photo = None
                if 'photo' in item and item['photo']:
                    if isinstance(item['photo'], dict):
                        photo = item['photo'].get('url') or item['photo'].get('full_size_url')
                    elif isinstance(item['photo'], str):
                        photo = item['photo']
                
                if 'photos' in item and item['photos'] and len(item['photos']) > 0:
                    first_photo = item['photos'][0]
                    if isinstance(first_photo, dict):
                        photo = first_photo.get('url') or first_photo.get('full_size_url')
                
                parsed.append({
                    'id': item_id,
                    'title': title,
                    'price': price,
                    'currency': currency,
                    'url': url,
                    'photo': photo
                })
            except:
                continue
        
        return parsed
    
    def fetch_vinted_items(self, url):
        """Recupera articoli da Vinted"""
        try:
            logger.info(f"🔍 Fetching: {url[:100]}")
            
            # Assicurati che l'URL sia corretto
            if not url.startswith('http'):
                url = 'https://' + url
            
            response = self.session.get(url, timeout=20, allow_redirects=True)
            logger.info(f"📊 Status: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ HTTP {response.status_code}")
                return []
            
            # Salva HTML per debug (prime 1000 chars)
            logger.info(f"📄 HTML preview: {response.text[:200]}")
            
            items = self.extract_json_from_html(response.text)
            
            if items:
                logger.info(f"✅ Trovati {len(items)} articoli!")
                for i, item in enumerate(items[:3], 1):
                    logger.info(f"  {i}. {item['title'][:40]} - {item['price']}{item['currency']}")
                    if item['photo']:
                        logger.info(f"     📸 Foto: {item['photo'][:60]}...")
                    else:
                        logger.info(f"     📸 Nessuna foto")
            else:
                logger.warning("⚠️ Nessun articolo estratto")
            
            return items
            
        except requests.RequestException as e:
            logger.error(f"❌ Errore connessione: {e}")
            return []
        except Exception as e:
            logger.error(f"❌ Errore generale: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def check_new_items(self, user_id, link_id):
        user_id = str(user_id)
        if user_id not in self.data['users']:
            return []
        
        link_data = self.data['users'][user_id]['links'].get(link_id)
        if not link_data:
            return []
        
        logger.info(f"🔍 Controllo #{link_id}: {link_data['name']}")
        
        current_items = self.fetch_vinted_items(link_data['url'])
        
        if not current_items:
            logger.warning("⚠️ Nessun articolo")
            return []
        
        current_ids = {item['id'] for item in current_items}
        last_ids = {item['id'] for item in link_data['last_items']}
        
        new_item_ids = current_ids - last_ids
        new_items = [item for item in current_items if item['id'] in new_item_ids]
        
        if new_items:
            logger.info(f"🆕 {len(new_items)} nuovi!")
        
        link_data['last_items'] = current_items
        link_data['last_check'] = datetime.now().isoformat()
        self.save_data()
        
        return new_items

monitor = VintedMonitor()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 <b>Bot Vinted Notifier</b>\n\n"
        "📋 Comandi:\n"
        "/aggiungi - Aggiungi link\n"
        "/lista - I tuoi link\n"
        "/test - Test\n"
        "/rimuovi - Rimuovi\n\n"
        "Inviami un link Vinted!",
        parse_mode='HTML'
    )

async def aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔗 Inviami il link tipo:\n"
        "<code>https://www.vinted.it/catalog?search_text=nike Nike</code>",
        parse_mode='HTML'
    )

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link. Usa /aggiungi!")
        return
    
    msg = "📋 <b>Link:</b>\n\n"
    for lid, data in links.items():
        msg += f"🔹 #{lid} - {data['name']}\n   📦 {len(data.get('last_items', []))} articoli\n\n"
    
    await update.message.reply_text(msg, parse_mode='HTML')

async def test_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link!")
        return
    
    await update.message.reply_text("🔍 Testing...")
    
    for lid, data in links.items():
        items = monitor.fetch_vinted_items(data['url'])
        
        if items:
            # Mostra statistiche generali
            await update.message.reply_text(
                f"✅ <b>{data['name']}</b>\n\n"
                f"Trovati {len(items)} articoli!\n\n"
                f"Ti mostro i primi 3:",
                parse_mode='HTML'
            )
            
            # Mostra i primi 3 articoli con foto
            for i, item in enumerate(items[:3], 1):
                caption = (
                    f"<b>{i}. {item['title']}</b>\n\n"
                    f"💰 <b>Prezzo:</b> {item['price']} {item['currency']}\n"
                    f"🔗 <a href='{item['url']}'>Vedi su Vinted</a>"
                )
                
                try:
                    if item['photo']:
                        await update.message.reply_photo(
                            photo=item['photo'],
                            caption=caption,
                            parse_mode='HTML'
                        )
                    else:
                        await update.message.reply_text(caption, parse_mode='HTML')
                except Exception as e:
                    logger.error(f"Errore invio foto: {e}")
                    await update.message.reply_text(caption, parse_mode='HTML')
                
                await asyncio.sleep(1)
        else:
            await update.message.reply_text(
                f"⚠️ <b>{data['name']}</b>\n\nNessun articolo trovato",
                parse_mode='HTML'
            )
        
        await asyncio.sleep(2)

async def rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link")
        return
    
    kb = [[InlineKeyboardButton(f"🗑️ {d['name']}", callback_data=f'remove_{lid}')] for lid, d in links.items()]
    kb.append([InlineKeyboardButton("❌ Annulla", callback_data='cancel')])
    
    await update.message.reply_text("Seleziona:", reply_markup=InlineKeyboardMarkup(kb))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if 'vinted' in text.lower():
        parts = text.split(' ', 1)
        url = parts[0]
        name = parts[1] if len(parts) > 1 else "Ricerca"
        
        await update.message.reply_text("🔍 Verifico...")
        
        items = monitor.fetch_vinted_items(url)
        link_id = monitor.add_user_link(update.effective_user.id, url, name)
        
        if items:
            await update.message.reply_text(
                f"✅ <b>Link aggiunto con successo!</b>\n\n"
                f"🏷️ <b>Nome:</b> {name}\n"
                f"🆔 <b>ID:</b> #{link_id}\n"
                f"📦 <b>Articoli trovati:</b> {len(items)}\n\n"
                f"Ecco i primi 3 articoli:",
                parse_mode='HTML'
            )
            
            # Mostra i primi 3 articoli con foto
            for i, item in enumerate(items[:3], 1):
                caption = (
                    f"<b>{item['title']}</b>\n\n"
                    f"💰 {item['price']} {item['currency']}\n"
                    f"🔗 <a href='{item['url']}'>Vedi</a>"
                )
                
                try:
                    if item['photo']:
                        await update.message.reply_photo(
                            photo=item['photo'],
                            caption=caption,
                            parse_mode='HTML'
                        )
                    else:
                        await update.message.reply_text(caption, parse_mode='HTML')
                except:
                    await update.message.reply_text(caption, parse_mode='HTML')
                
                await asyncio.sleep(0.5)
        else:
            await update.message.reply_text(
                f"⚠️ <b>Link aggiunto ma nessun articolo trovato.</b>\n\n"
                f"🏷️ {name}\n🆔 #{link_id}\n\n"
                f"Prova /test più tardi!",
                parse_mode='HTML'
            )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'cancel':
        await query.edit_message_text("❌ Annullato")
        return
    
    if query.data.startswith('remove_'):
        lid = query.data.replace('remove_', '')
        if monitor.remove_user_link(query.from_user.id, lid):
            await query.edit_message_text("✅ Rimosso!", parse_mode='HTML')

async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔍 CONTROLLO")
    
    for uid, udata in monitor.data['users'].items():
        for lid, ldata in udata['links'].items():
            try:
                new = monitor.check_new_items(uid, lid)
                
                for item in new:
                    msg = f"🆕 <b>{item['title']}</b>\n💰 {item['price']}{item['currency']}\n🔗 <a href='{item['url']}'>Vedi</a>"
                    
                    try:
                        if item['photo']:
                            await context.bot.send_photo(int(uid), item['photo'], caption=msg, parse_mode='HTML')
                        else:
                            await context.bot.send_message(int(uid), msg, parse_mode='HTML')
                    except:
                        pass
                
                await asyncio.sleep(3)
            except:
                pass
    
    logger.info("✅ FATTO")

def main():
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("❌ TOKEN!")
        return
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("aggiungi", aggiungi))
    app.add_handler(CommandHandler("lista", lista))
    app.add_handler(CommandHandler("test", test_link))
    app.add_handler(CommandHandler("rimuovi", rimuovi))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    app.job_queue.run_repeating(check_updates, interval=300, first=10)
    
    logger.info("🚀 BOT VINTED SCRAPER AVVIATO!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main(), text):
                                title = text
                                break
                    
                    # Cerca prezzo
                    price = "0"
                    currency = "€"
                    
                    if search_area:
                        # Cerca pattern prezzo in tutto il contenitore
                        all_text = search_area.get_text()
                        price_patterns = [
                            r'(\d+(?:[,.]\d+)?)\s*€',
                            r'€\s*(\d+(?:[,.]\d+)?)',
                            r'(\d+(?:[,.]\d+)?)\s*EUR',
                        ]
                        
                        for pattern in price_patterns:
                            price_match = re.search(pattern, all_text)
                            if price_match:
                                price = price_match.group(1).replace(',', '.')
                                currency = '€'
                                break
                    
                    # Cerca immagine
                    photo = None
                    if search_area:
                        img_tag = search_area.find('img')
                        if img_tag:
                            photo = img_tag.get('src') or img_tag.get('data-src') or img_tag.get('data-lazy-src')
                    
                    # Se non ha trovato nulla di utile, skip
                    if title == "Articolo" and price == "0":
                        continue
                    
                    items.append({
                        'id': str(item_id),
                        'title': title[:100],
                        'price': price,
                        'currency': currency,
                        'url': url,
                        'photo': photo
                    })
                    
                except Exception as e:
                    continue
            
            if items:
                logger.info(f"✅ Estratti {len(items)} articoli dal HTML!")
                return items[:20]
            
            logger.warning("❌ Nessun articolo trovato")
            return []
            
        except Exception as e:
            logger.error(f"❌ Errore parsing: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def _find_items_recursive(self, obj, depth=0, max_depth=15):
        """Cerca array 'items' ricorsivamente"""
        if depth > max_depth:
            return None
        
        if isinstance(obj, dict):
            # Cerca chiave 'items'
            if 'items' in obj and isinstance(obj['items'], list) and len(obj['items']) > 0:
                first = obj['items'][0]
                if isinstance(first, dict) and 'id' in first:
                    logger.info(f"✅ Trovato array items a profondità {depth}")
                    return self._parse_items_array(obj['items'])
            
            # Cerca in tutte le chiavi
            for key, value in obj.items():
                result = self._find_items_recursive(value, depth + 1, max_depth)
                if result:
                    return result
        
        elif isinstance(obj, list):
            for item in obj:
                result = self._find_items_recursive(item, depth + 1, max_depth)
                if result:
                    return result
        
        return None
    
    def _parse_items_array(self, items_array):
        """Parsea array di items in formato standard"""
        parsed = []
        
        for item in items_array[:20]:
            try:
                if not isinstance(item, dict) or 'id' not in item:
                    continue
                
                # ID
                item_id = str(item['id'])
                
                # Titolo
                title = item.get('title', 'Senza titolo')
                
                # Prezzo
                price = str(item.get('price', '0'))
                currency = item.get('currency', '€')
                
                # URL
                url = item.get('url', f"https://www.vinted.it/items/{item_id}")
                if not url.startswith('http'):
                    url = 'https://www.vinted.it' + url
                
                # Foto
                photo = None
                if 'photo' in item and item['photo']:
                    if isinstance(item['photo'], dict):
                        photo = item['photo'].get('url') or item['photo'].get('full_size_url')
                    elif isinstance(item['photo'], str):
                        photo = item['photo']
                
                if 'photos' in item and item['photos'] and len(item['photos']) > 0:
                    first_photo = item['photos'][0]
                    if isinstance(first_photo, dict):
                        photo = first_photo.get('url') or first_photo.get('full_size_url')
                
                parsed.append({
                    'id': item_id,
                    'title': title,
                    'price': price,
                    'currency': currency,
                    'url': url,
                    'photo': photo
                })
            except:
                continue
        
        return parsed
    
    def fetch_vinted_items(self, url):
        """Recupera articoli da Vinted"""
        try:
            logger.info(f"🔍 Fetching: {url[:100]}")
            
            # Assicurati che l'URL sia corretto
            if not url.startswith('http'):
                url = 'https://' + url
            
            response = self.session.get(url, timeout=20, allow_redirects=True)
            logger.info(f"📊 Status: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ HTTP {response.status_code}")
                return []
            
            # Salva HTML per debug (prime 1000 chars)
            logger.info(f"📄 HTML preview: {response.text[:200]}")
            
            items = self.extract_json_from_html(response.text)
            
            if items:
                logger.info(f"✅ Trovati {len(items)} articoli!")
                for i, item in enumerate(items[:3], 1):
                    logger.info(f"  {i}. {item['title'][:40]} - {item['price']}{item['currency']}")
            else:
                logger.warning("⚠️ Nessun articolo estratto")
            
            return items
            
        except requests.RequestException as e:
            logger.error(f"❌ Errore connessione: {e}")
            return []
        except Exception as e:
            logger.error(f"❌ Errore generale: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def check_new_items(self, user_id, link_id):
        user_id = str(user_id)
        if user_id not in self.data['users']:
            return []
        
        link_data = self.data['users'][user_id]['links'].get(link_id)
        if not link_data:
            return []
        
        logger.info(f"🔍 Controllo #{link_id}: {link_data['name']}")
        
        current_items = self.fetch_vinted_items(link_data['url'])
        
        if not current_items:
            logger.warning("⚠️ Nessun articolo")
            return []
        
        current_ids = {item['id'] for item in current_items}
        last_ids = {item['id'] for item in link_data['last_items']}
        
        new_item_ids = current_ids - last_ids
        new_items = [item for item in current_items if item['id'] in new_item_ids]
        
        if new_items:
            logger.info(f"🆕 {len(new_items)} nuovi!")
        
        link_data['last_items'] = current_items
        link_data['last_check'] = datetime.now().isoformat()
        self.save_data()
        
        return new_items

monitor = VintedMonitor()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 <b>Bot Vinted Notifier</b>\n\n"
        "📋 Comandi:\n"
        "/aggiungi - Aggiungi link\n"
        "/lista - I tuoi link\n"
        "/test - Test\n"
        "/rimuovi - Rimuovi\n\n"
        "Inviami un link Vinted!",
        parse_mode='HTML'
    )

async def aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔗 Inviami il link tipo:\n"
        "<code>https://www.vinted.it/catalog?search_text=nike Nike</code>",
        parse_mode='HTML'
    )

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link. Usa /aggiungi!")
        return
    
    msg = "📋 <b>Link:</b>\n\n"
    for lid, data in links.items():
        msg += f"🔹 #{lid} - {data['name']}\n   📦 {len(data.get('last_items', []))} articoli\n\n"
    
    await update.message.reply_text(msg, parse_mode='HTML')

async def test_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link!")
        return
    
    await update.message.reply_text("🔍 Testing...")
    
    for lid, data in links.items():
        items = monitor.fetch_vinted_items(data['url'])
        
        if items:
            msg = f"✅ <b>{data['name']}</b>\n\n{len(items)} articoli:\n"
            for i, item in enumerate(items[:3], 1):
                msg += f"{i}. {item['title'][:30]}... - {item['price']}{item['currency']}\n"
        else:
            msg = f"⚠️ <b>{data['name']}</b>\n\nNessun articolo trovato"
        
        await update.message.reply_text(msg, parse_mode='HTML')
        await asyncio.sleep(2)

async def rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link")
        return
    
    kb = [[InlineKeyboardButton(f"🗑️ {d['name']}", callback_data=f'remove_{lid}')] for lid, d in links.items()]
    kb.append([InlineKeyboardButton("❌ Annulla", callback_data='cancel')])
    
    await update.message.reply_text("Seleziona:", reply_markup=InlineKeyboardMarkup(kb))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if 'vinted' in text.lower():
        parts = text.split(' ', 1)
        url = parts[0]
        name = parts[1] if len(parts) > 1 else "Ricerca"
        
        await update.message.reply_text("🔍 Verifico...")
        
        items = monitor.fetch_vinted_items(url)
        link_id = monitor.add_user_link(update.effective_user.id, url, name)
        
        if items:
            await update.message.reply_text(
                f"✅ Link aggiunto!\n\n🏷️ {name}\n🆔 #{link_id}\n📦 {len(items)} articoli",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                f"⚠️ Link aggiunto ma nessun articolo trovato.\n\n🏷️ {name}\n🆔 #{link_id}\n\nProva /test più tardi!",
                parse_mode='HTML'
            )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'cancel':
        await query.edit_message_text("❌ Annullato")
        return
    
    if query.data.startswith('remove_'):
        lid = query.data.replace('remove_', '')
        if monitor.remove_user_link(query.from_user.id, lid):
            await query.edit_message_text("✅ Rimosso!", parse_mode='HTML')

async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔍 CONTROLLO")
    
    for uid, udata in monitor.data['users'].items():
        for lid, ldata in udata['links'].items():
            try:
                new = monitor.check_new_items(uid, lid)
                
                for item in new:
                    msg = f"🆕 <b>{item['title']}</b>\n💰 {item['price']}{item['currency']}\n🔗 <a href='{item['url']}'>Vedi</a>"
                    
                    try:
                        if item['photo']:
                            await context.bot.send_photo(int(uid), item['photo'], caption=msg, parse_mode='HTML')
                        else:
                            await context.bot.send_message(int(uid), msg, parse_mode='HTML')
                    except:
                        pass
                
                await asyncio.sleep(3)
            except:
                pass
    
    logger.info("✅ FATTO")

def main():
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("❌ TOKEN!")
        return
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("aggiungi", aggiungi))
    app.add_handler(CommandHandler("lista", lista))
    app.add_handler(CommandHandler("test", test_link))
    app.add_handler(CommandHandler("rimuovi", rimuovi))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    app.job_queue.run_repeating(check_updates, interval=300, first=10)
    
    logger.info("🚀 BOT VINTED SCRAPER AVVIATO!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
                            if len(text) > 10 and not re.match(price_pattern, text):
                                title = text
                                break len(text) > 10 and not re.match(r'^[\d,\.€$£\s]+
                    
                except Exception as e:
                    continue
            
            if items:
                logger.info(f"✅ Estratti {len(items)} articoli dal HTML!")
                return items[:20]
            
            logger.warning("❌ Nessun articolo trovato")
            return []
            
        except Exception as e:
            logger.error(f"❌ Errore parsing: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def _find_items_recursive(self, obj, depth=0, max_depth=15):
        """Cerca array 'items' ricorsivamente"""
        if depth > max_depth:
            return None
        
        if isinstance(obj, dict):
            # Cerca chiave 'items'
            if 'items' in obj and isinstance(obj['items'], list) and len(obj['items']) > 0:
                first = obj['items'][0]
                if isinstance(first, dict) and 'id' in first:
                    logger.info(f"✅ Trovato array items a profondità {depth}")
                    return self._parse_items_array(obj['items'])
            
            # Cerca in tutte le chiavi
            for key, value in obj.items():
                result = self._find_items_recursive(value, depth + 1, max_depth)
                if result:
                    return result
        
        elif isinstance(obj, list):
            for item in obj:
                result = self._find_items_recursive(item, depth + 1, max_depth)
                if result:
                    return result
        
        return None
    
    def _parse_items_array(self, items_array):
        """Parsea array di items in formato standard"""
        parsed = []
        
        for item in items_array[:20]:
            try:
                if not isinstance(item, dict) or 'id' not in item:
                    continue
                
                # ID
                item_id = str(item['id'])
                
                # Titolo
                title = item.get('title', 'Senza titolo')
                
                # Prezzo
                price = str(item.get('price', '0'))
                currency = item.get('currency', '€')
                
                # URL
                url = item.get('url', f"https://www.vinted.it/items/{item_id}")
                if not url.startswith('http'):
                    url = 'https://www.vinted.it' + url
                
                # Foto
                photo = None
                if 'photo' in item and item['photo']:
                    if isinstance(item['photo'], dict):
                        photo = item['photo'].get('url') or item['photo'].get('full_size_url')
                    elif isinstance(item['photo'], str):
                        photo = item['photo']
                
                if 'photos' in item and item['photos'] and len(item['photos']) > 0:
                    first_photo = item['photos'][0]
                    if isinstance(first_photo, dict):
                        photo = first_photo.get('url') or first_photo.get('full_size_url')
                
                parsed.append({
                    'id': item_id,
                    'title': title,
                    'price': price,
                    'currency': currency,
                    'url': url,
                    'photo': photo
                })
            except:
                continue
        
        return parsed
    
    def fetch_vinted_items(self, url):
        """Recupera articoli da Vinted"""
        try:
            logger.info(f"🔍 Fetching: {url[:100]}")
            
            # Assicurati che l'URL sia corretto
            if not url.startswith('http'):
                url = 'https://' + url
            
            response = self.session.get(url, timeout=20, allow_redirects=True)
            logger.info(f"📊 Status: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ HTTP {response.status_code}")
                return []
            
            # Salva HTML per debug (prime 1000 chars)
            logger.info(f"📄 HTML preview: {response.text[:200]}")
            
            items = self.extract_json_from_html(response.text)
            
            if items:
                logger.info(f"✅ Trovati {len(items)} articoli!")
                for i, item in enumerate(items[:3], 1):
                    logger.info(f"  {i}. {item['title'][:40]} - {item['price']}{item['currency']}")
                    if item['photo']:
                        logger.info(f"     📸 Foto: {item['photo'][:60]}...")
                    else:
                        logger.info(f"     📸 Nessuna foto")
            else:
                logger.warning("⚠️ Nessun articolo estratto")
            
            return items
            
        except requests.RequestException as e:
            logger.error(f"❌ Errore connessione: {e}")
            return []
        except Exception as e:
            logger.error(f"❌ Errore generale: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def check_new_items(self, user_id, link_id):
        user_id = str(user_id)
        if user_id not in self.data['users']:
            return []
        
        link_data = self.data['users'][user_id]['links'].get(link_id)
        if not link_data:
            return []
        
        logger.info(f"🔍 Controllo #{link_id}: {link_data['name']}")
        
        current_items = self.fetch_vinted_items(link_data['url'])
        
        if not current_items:
            logger.warning("⚠️ Nessun articolo")
            return []
        
        current_ids = {item['id'] for item in current_items}
        last_ids = {item['id'] for item in link_data['last_items']}
        
        new_item_ids = current_ids - last_ids
        new_items = [item for item in current_items if item['id'] in new_item_ids]
        
        if new_items:
            logger.info(f"🆕 {len(new_items)} nuovi!")
        
        link_data['last_items'] = current_items
        link_data['last_check'] = datetime.now().isoformat()
        self.save_data()
        
        return new_items

monitor = VintedMonitor()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 <b>Bot Vinted Notifier</b>\n\n"
        "📋 Comandi:\n"
        "/aggiungi - Aggiungi link\n"
        "/lista - I tuoi link\n"
        "/test - Test\n"
        "/rimuovi - Rimuovi\n\n"
        "Inviami un link Vinted!",
        parse_mode='HTML'
    )

async def aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔗 Inviami il link tipo:\n"
        "<code>https://www.vinted.it/catalog?search_text=nike Nike</code>",
        parse_mode='HTML'
    )

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link. Usa /aggiungi!")
        return
    
    msg = "📋 <b>Link:</b>\n\n"
    for lid, data in links.items():
        msg += f"🔹 #{lid} - {data['name']}\n   📦 {len(data.get('last_items', []))} articoli\n\n"
    
    await update.message.reply_text(msg, parse_mode='HTML')

async def test_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link!")
        return
    
    await update.message.reply_text("🔍 Testing...")
    
    for lid, data in links.items():
        items = monitor.fetch_vinted_items(data['url'])
        
        if items:
            # Mostra statistiche generali
            await update.message.reply_text(
                f"✅ <b>{data['name']}</b>\n\n"
                f"Trovati {len(items)} articoli!\n\n"
                f"Ti mostro i primi 3:",
                parse_mode='HTML'
            )
            
            # Mostra i primi 3 articoli con foto
            for i, item in enumerate(items[:3], 1):
                caption = (
                    f"<b>{i}. {item['title']}</b>\n\n"
                    f"💰 <b>Prezzo:</b> {item['price']} {item['currency']}\n"
                    f"🔗 <a href='{item['url']}'>Vedi su Vinted</a>"
                )
                
                try:
                    if item['photo']:
                        await update.message.reply_photo(
                            photo=item['photo'],
                            caption=caption,
                            parse_mode='HTML'
                        )
                    else:
                        await update.message.reply_text(caption, parse_mode='HTML')
                except Exception as e:
                    logger.error(f"Errore invio foto: {e}")
                    await update.message.reply_text(caption, parse_mode='HTML')
                
                await asyncio.sleep(1)
        else:
            await update.message.reply_text(
                f"⚠️ <b>{data['name']}</b>\n\nNessun articolo trovato",
                parse_mode='HTML'
            )
        
        await asyncio.sleep(2)

async def rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link")
        return
    
    kb = [[InlineKeyboardButton(f"🗑️ {d['name']}", callback_data=f'remove_{lid}')] for lid, d in links.items()]
    kb.append([InlineKeyboardButton("❌ Annulla", callback_data='cancel')])
    
    await update.message.reply_text("Seleziona:", reply_markup=InlineKeyboardMarkup(kb))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if 'vinted' in text.lower():
        parts = text.split(' ', 1)
        url = parts[0]
        name = parts[1] if len(parts) > 1 else "Ricerca"
        
        await update.message.reply_text("🔍 Verifico...")
        
        items = monitor.fetch_vinted_items(url)
        link_id = monitor.add_user_link(update.effective_user.id, url, name)
        
        if items:
            await update.message.reply_text(
                f"✅ <b>Link aggiunto con successo!</b>\n\n"
                f"🏷️ <b>Nome:</b> {name}\n"
                f"🆔 <b>ID:</b> #{link_id}\n"
                f"📦 <b>Articoli trovati:</b> {len(items)}\n\n"
                f"Ecco i primi 3 articoli:",
                parse_mode='HTML'
            )
            
            # Mostra i primi 3 articoli con foto
            for i, item in enumerate(items[:3], 1):
                caption = (
                    f"<b>{item['title']}</b>\n\n"
                    f"💰 {item['price']} {item['currency']}\n"
                    f"🔗 <a href='{item['url']}'>Vedi</a>"
                )
                
                try:
                    if item['photo']:
                        await update.message.reply_photo(
                            photo=item['photo'],
                            caption=caption,
                            parse_mode='HTML'
                        )
                    else:
                        await update.message.reply_text(caption, parse_mode='HTML')
                except:
                    await update.message.reply_text(caption, parse_mode='HTML')
                
                await asyncio.sleep(0.5)
        else:
            await update.message.reply_text(
                f"⚠️ <b>Link aggiunto ma nessun articolo trovato.</b>\n\n"
                f"🏷️ {name}\n🆔 #{link_id}\n\n"
                f"Prova /test più tardi!",
                parse_mode='HTML'
            )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'cancel':
        await query.edit_message_text("❌ Annullato")
        return
    
    if query.data.startswith('remove_'):
        lid = query.data.replace('remove_', '')
        if monitor.remove_user_link(query.from_user.id, lid):
            await query.edit_message_text("✅ Rimosso!", parse_mode='HTML')

async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔍 CONTROLLO")
    
    for uid, udata in monitor.data['users'].items():
        for lid, ldata in udata['links'].items():
            try:
                new = monitor.check_new_items(uid, lid)
                
                for item in new:
                    msg = f"🆕 <b>{item['title']}</b>\n💰 {item['price']}{item['currency']}\n🔗 <a href='{item['url']}'>Vedi</a>"
                    
                    try:
                        if item['photo']:
                            await context.bot.send_photo(int(uid), item['photo'], caption=msg, parse_mode='HTML')
                        else:
                            await context.bot.send_message(int(uid), msg, parse_mode='HTML')
                    except:
                        pass
                
                await asyncio.sleep(3)
            except:
                pass
    
    logger.info("✅ FATTO")

def main():
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("❌ TOKEN!")
        return
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("aggiungi", aggiungi))
    app.add_handler(CommandHandler("lista", lista))
    app.add_handler(CommandHandler("test", test_link))
    app.add_handler(CommandHandler("rimuovi", rimuovi))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    app.job_queue.run_repeating(check_updates, interval=300, first=10)
    
    logger.info("🚀 BOT VINTED SCRAPER AVVIATO!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main(), text):
                                title = text
                                break
                    
                    # Cerca prezzo
                    price = "0"
                    currency = "€"
                    
                    if search_area:
                        # Cerca pattern prezzo in tutto il contenitore
                        all_text = search_area.get_text()
                        price_patterns = [
                            r'(\d+(?:[,.]\d+)?)\s*€',
                            r'€\s*(\d+(?:[,.]\d+)?)',
                            r'(\d+(?:[,.]\d+)?)\s*EUR',
                        ]
                        
                        for pattern in price_patterns:
                            price_match = re.search(pattern, all_text)
                            if price_match:
                                price = price_match.group(1).replace(',', '.')
                                currency = '€'
                                break
                    
                    # Cerca immagine
                    photo = None
                    if search_area:
                        img_tag = search_area.find('img')
                        if img_tag:
                            photo = img_tag.get('src') or img_tag.get('data-src') or img_tag.get('data-lazy-src')
                    
                    # Se non ha trovato nulla di utile, skip
                    if title == "Articolo" and price == "0":
                        continue
                    
                    items.append({
                        'id': str(item_id),
                        'title': title[:100],
                        'price': price,
                        'currency': currency,
                        'url': url,
                        'photo': photo
                    })
                    
                except Exception as e:
                    continue
            
            if items:
                logger.info(f"✅ Estratti {len(items)} articoli dal HTML!")
                return items[:20]
            
            logger.warning("❌ Nessun articolo trovato")
            return []
            
        except Exception as e:
            logger.error(f"❌ Errore parsing: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def _find_items_recursive(self, obj, depth=0, max_depth=15):
        """Cerca array 'items' ricorsivamente"""
        if depth > max_depth:
            return None
        
        if isinstance(obj, dict):
            # Cerca chiave 'items'
            if 'items' in obj and isinstance(obj['items'], list) and len(obj['items']) > 0:
                first = obj['items'][0]
                if isinstance(first, dict) and 'id' in first:
                    logger.info(f"✅ Trovato array items a profondità {depth}")
                    return self._parse_items_array(obj['items'])
            
            # Cerca in tutte le chiavi
            for key, value in obj.items():
                result = self._find_items_recursive(value, depth + 1, max_depth)
                if result:
                    return result
        
        elif isinstance(obj, list):
            for item in obj:
                result = self._find_items_recursive(item, depth + 1, max_depth)
                if result:
                    return result
        
        return None
    
    def _parse_items_array(self, items_array):
        """Parsea array di items in formato standard"""
        parsed = []
        
        for item in items_array[:20]:
            try:
                if not isinstance(item, dict) or 'id' not in item:
                    continue
                
                # ID
                item_id = str(item['id'])
                
                # Titolo
                title = item.get('title', 'Senza titolo')
                
                # Prezzo
                price = str(item.get('price', '0'))
                currency = item.get('currency', '€')
                
                # URL
                url = item.get('url', f"https://www.vinted.it/items/{item_id}")
                if not url.startswith('http'):
                    url = 'https://www.vinted.it' + url
                
                # Foto
                photo = None
                if 'photo' in item and item['photo']:
                    if isinstance(item['photo'], dict):
                        photo = item['photo'].get('url') or item['photo'].get('full_size_url')
                    elif isinstance(item['photo'], str):
                        photo = item['photo']
                
                if 'photos' in item and item['photos'] and len(item['photos']) > 0:
                    first_photo = item['photos'][0]
                    if isinstance(first_photo, dict):
                        photo = first_photo.get('url') or first_photo.get('full_size_url')
                
                parsed.append({
                    'id': item_id,
                    'title': title,
                    'price': price,
                    'currency': currency,
                    'url': url,
                    'photo': photo
                })
            except:
                continue
        
        return parsed
    
    def fetch_vinted_items(self, url):
        """Recupera articoli da Vinted"""
        try:
            logger.info(f"🔍 Fetching: {url[:100]}")
            
            # Assicurati che l'URL sia corretto
            if not url.startswith('http'):
                url = 'https://' + url
            
            response = self.session.get(url, timeout=20, allow_redirects=True)
            logger.info(f"📊 Status: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ HTTP {response.status_code}")
                return []
            
            # Salva HTML per debug (prime 1000 chars)
            logger.info(f"📄 HTML preview: {response.text[:200]}")
            
            items = self.extract_json_from_html(response.text)
            
            if items:
                logger.info(f"✅ Trovati {len(items)} articoli!")
                for i, item in enumerate(items[:3], 1):
                    logger.info(f"  {i}. {item['title'][:40]} - {item['price']}{item['currency']}")
            else:
                logger.warning("⚠️ Nessun articolo estratto")
            
            return items
            
        except requests.RequestException as e:
            logger.error(f"❌ Errore connessione: {e}")
            return []
        except Exception as e:
            logger.error(f"❌ Errore generale: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def check_new_items(self, user_id, link_id):
        user_id = str(user_id)
        if user_id not in self.data['users']:
            return []
        
        link_data = self.data['users'][user_id]['links'].get(link_id)
        if not link_data:
            return []
        
        logger.info(f"🔍 Controllo #{link_id}: {link_data['name']}")
        
        current_items = self.fetch_vinted_items(link_data['url'])
        
        if not current_items:
            logger.warning("⚠️ Nessun articolo")
            return []
        
        current_ids = {item['id'] for item in current_items}
        last_ids = {item['id'] for item in link_data['last_items']}
        
        new_item_ids = current_ids - last_ids
        new_items = [item for item in current_items if item['id'] in new_item_ids]
        
        if new_items:
            logger.info(f"🆕 {len(new_items)} nuovi!")
        
        link_data['last_items'] = current_items
        link_data['last_check'] = datetime.now().isoformat()
        self.save_data()
        
        return new_items

monitor = VintedMonitor()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 <b>Bot Vinted Notifier</b>\n\n"
        "📋 Comandi:\n"
        "/aggiungi - Aggiungi link\n"
        "/lista - I tuoi link\n"
        "/test - Test\n"
        "/rimuovi - Rimuovi\n\n"
        "Inviami un link Vinted!",
        parse_mode='HTML'
    )

async def aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔗 Inviami il link tipo:\n"
        "<code>https://www.vinted.it/catalog?search_text=nike Nike</code>",
        parse_mode='HTML'
    )

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link. Usa /aggiungi!")
        return
    
    msg = "📋 <b>Link:</b>\n\n"
    for lid, data in links.items():
        msg += f"🔹 #{lid} - {data['name']}\n   📦 {len(data.get('last_items', []))} articoli\n\n"
    
    await update.message.reply_text(msg, parse_mode='HTML')

async def test_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link!")
        return
    
    await update.message.reply_text("🔍 Testing...")
    
    for lid, data in links.items():
        items = monitor.fetch_vinted_items(data['url'])
        
        if items:
            msg = f"✅ <b>{data['name']}</b>\n\n{len(items)} articoli:\n"
            for i, item in enumerate(items[:3], 1):
                msg += f"{i}. {item['title'][:30]}... - {item['price']}{item['currency']}\n"
        else:
            msg = f"⚠️ <b>{data['name']}</b>\n\nNessun articolo trovato"
        
        await update.message.reply_text(msg, parse_mode='HTML')
        await asyncio.sleep(2)

async def rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link")
        return
    
    kb = [[InlineKeyboardButton(f"🗑️ {d['name']}", callback_data=f'remove_{lid}')] for lid, d in links.items()]
    kb.append([InlineKeyboardButton("❌ Annulla", callback_data='cancel')])
    
    await update.message.reply_text("Seleziona:", reply_markup=InlineKeyboardMarkup(kb))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if 'vinted' in text.lower():
        parts = text.split(' ', 1)
        url = parts[0]
        name = parts[1] if len(parts) > 1 else "Ricerca"
        
        await update.message.reply_text("🔍 Verifico...")
        
        items = monitor.fetch_vinted_items(url)
        link_id = monitor.add_user_link(update.effective_user.id, url, name)
        
        if items:
            await update.message.reply_text(
                f"✅ Link aggiunto!\n\n🏷️ {name}\n🆔 #{link_id}\n📦 {len(items)} articoli",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                f"⚠️ Link aggiunto ma nessun articolo trovato.\n\n🏷️ {name}\n🆔 #{link_id}\n\nProva /test più tardi!",
                parse_mode='HTML'
            )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'cancel':
        await query.edit_message_text("❌ Annullato")
        return
    
    if query.data.startswith('remove_'):
        lid = query.data.replace('remove_', '')
        if monitor.remove_user_link(query.from_user.id, lid):
            await query.edit_message_text("✅ Rimosso!", parse_mode='HTML')

async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔍 CONTROLLO")
    
    for uid, udata in monitor.data['users'].items():
        for lid, ldata in udata['links'].items():
            try:
                new = monitor.check_new_items(uid, lid)
                
                for item in new:
                    msg = f"🆕 <b>{item['title']}</b>\n💰 {item['price']}{item['currency']}\n🔗 <a href='{item['url']}'>Vedi</a>"
                    
                    try:
                        if item['photo']:
                            await context.bot.send_photo(int(uid), item['photo'], caption=msg, parse_mode='HTML')
                        else:
                            await context.bot.send_message(int(uid), msg, parse_mode='HTML')
                    except:
                        pass
                
                await asyncio.sleep(3)
            except:
                pass
    
    logger.info("✅ FATTO")

def main():
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("❌ TOKEN!")
        return
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("aggiungi", aggiungi))
    app.add_handler(CommandHandler("lista", lista))
    app.add_handler(CommandHandler("test", test_link))
    app.add_handler(CommandHandler("rimuovi", rimuovi))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    app.job_queue.run_repeating(check_updates, interval=300, first=10)
    
    logger.info("🚀 BOT VINTED SCRAPER AVVIATO!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
