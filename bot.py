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

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_FILE = 'vinted_data.json'

class VintedMonitor:
    def __init__(self):
        self.data = self.load_data()
        self.session = requests.Session()
        self._setup_session()
    
    def _setup_session(self):
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'it-IT,it;q=0.9',
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
    
    def add_user_link(self, user_id, link, name, interval=180):
        user_id = str(user_id)
        if user_id not in self.data['users']:
            self.data['users'][user_id] = {'links': {}}
        link_id = str(len(self.data['users'][user_id]['links']) + 1)
        self.data['users'][user_id]['links'][link_id] = {
            'url': link, 
            'name': name, 
            'last_items': [], 
            'added_at': datetime.now().isoformat(),
            'check_interval': interval
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
        return self.data['users'].get(user_id, {}).get('links', {})
    
    def extract_price(self, text):
        """Estrae il prezzo in tutti i formati possibili"""
        # Pulisci il testo
        text = text.replace('\n', ' ').replace('\r', ' ')
        
        # Pattern multipli per catturare OGNI formato di prezzo
        patterns = [
            r'€\s*(\d+[,.]?\d*)',                    # €250.00 o €250,00
            r'(\d+[,.]?\d*)\s*€',                    # 250.00€ o 250,00€  
            r'(\d+[,.]?\d*)\s*EUR',                  # 250.00 EUR
            r'EUR\s*(\d+[,.]?\d*)',                  # EUR 250.00
            r'[Pp]rice[:\s]+(\d+[,.]?\d*)',          # Price: 250.00
            r'[Pp]rezzo[:\s]+(\d+[,.]?\d*)',         # Prezzo: 250.00
            r'[Cc]ost[oa][:\s]+(\d+[,.]?\d*)',       # Costa: 250.00
            r'(\d+[,.]?\d*)\s*euro',                 # 250 euro
            r'include.*?(\d+[,.]?\d*)',              # include...250
        ]
        
        all_prices = []
        
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                price_str = match.group(1).replace(',', '.')
                try:
                    price_val = float(price_str)
                    if 0.01 <= price_val <= 99999:
                        all_prices.append(price_val)
                except:
                    continue
        
        if all_prices:
            # Prendi il prezzo più alto (di solito è quello con Protezione)
            # oppure se c'è solo uno, quello
            return f"{max(all_prices):.2f}"
        
        return None
    
    def fetch_vinted_items(self, url):
        try:
            logger.info(f"🔍 Fetching: {url[:100]}")
            response = self.session.get(url, timeout=20)
            logger.info(f"📊 Status: {response.status_code}")
            
            if response.status_code != 200:
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Metodo 1: Cerca JSON embedded
            scripts = soup.find_all('script')
            for script in scripts:
                if not script.string:
                    continue
                
                # Cerca window.__NUXT__ o simili
                if 'window.__NUXT__' in script.string or 'window.__INITIAL_STATE__' in script.string:
                    try:
                        # Estrai il JSON
                        json_match = re.search(r'=\s*({.+})', script.string, re.DOTALL)
                        if json_match:
                            data = json.loads(json_match.group(1).rstrip(';'))
                            items = self._extract_items_from_json(data)
                            if items:
                                logger.info(f"✅ Trovati {len(items)} articoli da JSON!")
                                return items
                    except:
                        continue
            
            # Metodo 2: Parsing HTML
            logger.info("🔍 Parsing HTML...")
            all_links = soup.find_all('a', href=re.compile(r'/items/\d+'))
            logger.info(f"🔗 Link articoli: {len(all_links)}")
            
            items = []
            seen_ids = set()
            
            for link in all_links[:40]:
                try:
                    href = link.get('href', '')
                    id_match = re.search(r'/items/(\d+)', href)
                    if not id_match or id_match.group(1) in seen_ids:
                        continue
                    
                    item_id = id_match.group(1)
                    seen_ids.add(item_id)
                    url = href if href.startswith('http') else f"https://www.vinted.it{href}"
                    
                    # Cerca nel parent del link
                    container = link.find_parent(['div', 'article', 'li'])
                    if not container:
                        container = link
                    
                    # Estrai tutto il testo del container E della descrizione
                    full_text = container.get_text(separator=' ')
                    
                    # Cerca anche nel titolo completo e descrizione
                    title_elem = container.find(['h1', 'h2', 'h3', 'h4'])
                    desc_elem = container.find(['p', 'div'], class_=re.compile(r'description|desc', re.I))
                    
                    if title_elem:
                        full_text += ' ' + title_elem.get_text()
                    if desc_elem:
                        full_text += ' ' + desc_elem.get_text()
                    
                    # Titolo
                    title = link.get('title') or link.get_text(strip=True) or "Articolo"
                    if len(title) < 5 or title.isdigit():
                        # Cerca nel container escludendo numeri isolati
                        for elem in container.find_all(['div', 'p', 'span', 'h2', 'h3', 'h4']):
                            txt = elem.get_text(strip=True)
                            if 10 < len(txt) < 150 and not txt.replace(' ','').isdigit():
                                # Escludi se sembra un prezzo
                                if not re.search(r'^\d+[,.]?\d*\s*€', txt):
                                    title = txt
                                    break
                    
                    # Prezzo: cerca in tutto il container
                    price = self.extract_price(full_text)
                    if not price:
                        # Cerca anche negli attributi
                        price_elem = container.find(attrs={'data-testid': re.compile('price', re.I)})
                        if price_elem:
                            price = self.extract_price(price_elem.get_text())
                    
                    # Foto
                    photo = None
                    img = container.find('img')
                    if img:
                        photo = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                        # Se è placeholder, cerca lazy load
                        if photo and ('placeholder' in photo or 'data:image' in photo):
                            photo = img.get('data-src') or img.get('data-lazy-src')
                    
                    # Aggiungi solo se ha almeno titolo O prezzo
                    if (title != "Articolo" and len(title) > 5) or price:
                        items.append({
                            'id': item_id,
                            'title': title[:120],
                            'price': price or "N/D",
                            'currency': '€',
                            'url': url,
                            'photo': photo
                        })
                        logger.info(f"  ✓ {title[:40]} - {price or 'N/D'}€")
                except Exception as e:
                    logger.error(f"  ✗ Errore parsing item: {e}")
                    continue
            
            logger.info(f"✅ Totale: {len(items)} articoli estratti")
            return items[:25]
            
        except Exception as e:
            logger.error(f"❌ Errore fetch: {e}")
            return []
    
    def _extract_items_from_json(self, data, depth=0, max_depth=10):
        """Estrae items da JSON ricorsivamente"""
        if depth > max_depth:
            return None
        
        if isinstance(data, dict):
            if 'items' in data and isinstance(data['items'], list):
                items = []
                for item in data['items'][:25]:
                    if not isinstance(item, dict) or 'id' not in item:
                        continue
                    
                    price = str(item.get('price', '0'))
                    if 'total_item_price' in item:
                        price = str(item['total_item_price'])
                    
                    photo = None
                    if 'photo' in item and isinstance(item['photo'], dict):
                        photo = item['photo'].get('url')
                    
                    items.append({
                        'id': str(item['id']),
                        'title': item.get('title', 'Articolo')[:120],
                        'price': price,
                        'currency': item.get('currency', '€'),
                        'url': item.get('url', f"https://www.vinted.it/items/{item['id']}"),
                        'photo': photo
                    })
                
                return items if items else None
            
            for value in data.values():
                result = self._extract_items_from_json(value, depth + 1, max_depth)
                if result:
                    return result
        
        elif isinstance(data, list):
            for item in data:
                result = self._extract_items_from_json(item, depth + 1, max_depth)
                if result:
                    return result
        
        return None
    
    def check_new_items(self, user_id, link_id):
        user_id = str(user_id)
        if user_id not in self.data['users']:
            return []
        
        link_data = self.data['users'][user_id]['links'].get(link_id)
        if not link_data:
            return []
        
        logger.info(f"🔍 Check link #{link_id}: {link_data['name']}")
        current = self.fetch_vinted_items(link_data['url'])
        
        if not current:
            return []
        
        current_ids = {i['id'] for i in current}
        last_ids = {i['id'] for i in link_data['last_items']}
        new_ids = current_ids - last_ids
        new_items = [i for i in current if i['id'] in new_ids]
        
        if new_items:
            logger.info(f"🆕 {len(new_items)} nuovi articoli!")
        
        link_data['last_items'] = current
        link_data['last_check'] = datetime.now().isoformat()
        self.save_data()
        
        return new_items

monitor = VintedMonitor()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛍️ <b>Benvenuto su Vinted Alert Bot!</b>\n\n"
        "🔔 Ti avviso quando compaiono nuovi articoli!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📋 <b>Comandi:</b>\n\n"
        "  /aggiungi - 🔗 Aggiungi ricerca\n"
        "  /lista - 📜 Vedi i tuoi link\n"
        "  /test - 🔍 Test immediato\n"
        "  /rimuovi - 🗑️ Elimina link\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⏱️ Controllo ogni 3 minuti\n"
        "🚀 Pronto ad iniziare!",
        parse_mode='HTML'
    )

async def aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔗 <b>Aggiungi un nuovo link</b>\n\n"
        "Copia il link da Vinted e inviamelo insieme al nome:\n\n"
        "📝 <b>Formato:</b>\n"
        "<code>https://www.vinted.it/catalog?... Nome</code>\n\n"
        "💡 <b>Esempio:</b>\n"
        "<code>https://www.vinted.it/catalog?search_text=switch Switch Lite</code>",
        parse_mode='HTML'
    )

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    if not links:
        await update.message.reply_text(
            "📭 <b>Nessuna ricerca attiva</b>\n\n"
            "Usa /aggiungi per iniziare!",
            parse_mode='HTML'
        )
        return
    
    msg = "📋 <b>Le tue ricerche attive:</b>\n\n"
    for lid, d in links.items():
        last_check = d.get('last_check', 'Mai')
        if last_check != 'Mai':
            last_check = last_check[11:16]
        msg += f"🔹 <b>#{lid}</b> • {d['name']}\n"
        msg += f"   📦 {len(d.get('last_items', []))} articoli trovati\n"
        msg += f"   🕐 Ultimo check: {last_check}\n\n"
    
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "💡 Usa /test per vedere gli articoli\n"
    msg += "🗑️ Usa /rimuovi per eliminare"
    
    await update.message.reply_text(msg, parse_mode='HTML')

async def test_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    if not links:
        await update.message.reply_text("📭 Nessun link da testare")
        return
    
    await update.message.reply_text("🔍 <b>Sto cercando articoli...</b>", parse_mode='HTML')
    
    for lid, data in links.items():
        items = monitor.fetch_vinted_items(data['url'])
        
        if items:
            await update.message.reply_text(
                f"✅ <b>{data['name']}</b>\n\n"
                f"📦 Trovati {len(items)} articoli!\n"
                f"━━━━━━━━━━━━━━━━━━━━",
                parse_mode='HTML'
            )
            
            for i, item in enumerate(items[:5], 1):
                caption = (
                    f"<b>{item['title']}</b>\n\n"
                    f"💰 <b>Prezzo:</b> {item['price']} €\n"
                    f"🔗 <a href='{item['url']}'>Vedi su Vinted</a>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📍 Articolo {i} di {min(5, len(items))}"
                )
                
                try:
                    if item['photo'] and 'http' in item['photo']:
                        await update.message.reply_photo(item['photo'], caption=caption, parse_mode='HTML')
                    else:
                        await update.message.reply_text(caption, parse_mode='HTML')
                except:
                    await update.message.reply_text(caption, parse_mode='HTML')
                
                await asyncio.sleep(0.8)
        else:
            await update.message.reply_text(
                f"⚠️ <b>{data['name']}</b>\n\nNessun articolo trovato al momento",
                parse_mode='HTML'
            )

async def rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    if not links:
        await update.message.reply_text("📭 Nessun link")
        return
    
    kb = [[InlineKeyboardButton(f"🗑️ {d['name']}", callback_data=f'remove_{lid}')] for lid, d in links.items()]
    kb.append([InlineKeyboardButton("❌ Annulla", callback_data='cancel')])
    
    await update.message.reply_text(
        "🗑️ <b>Seleziona quale rimuovere:</b>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='HTML'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    # Se sta aspettando l'intervallo
    if context.user_data.get('awaiting_interval'):
        try:
            interval = int(text)
            if interval < 1 or interval > 60:
                await update.message.reply_text("❌ Inserisci un numero tra 1 e 60 minuti!")
                return
            
            # Recupera i dati temporanei
            url = context.user_data.get('temp_url')
            name = context.user_data.get('temp_name')
            
            msg = await update.message.reply_text("🔍 <b>Verifico il link...</b>", parse_mode='HTML')
            
            items = monitor.fetch_vinted_items(url)
            link_id = monitor.add_user_link(user_id, url, name, interval * 60)
            
            await msg.edit_text(
                f"✅ <b>Link aggiunto con successo!</b>\n\n"
                f"🏷️ <b>Nome:</b> {name}\n"
                f"🆔 <b>ID:</b> #{link_id}\n"
                f"📦 <b>Articoli:</b> {len(items)}\n"
                f"⏱️ <b>Controllo ogni:</b> {interval} minuti\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔔 Ti avviserò per nuovi articoli!",
                parse_mode='HTML'
            )
            
            if items:
                for i, item in enumerate(items[:3], 1):
                    caption = (
                        f"<b>{item['title']}</b>\n\n"
                        f"💰 <b>Prezzo:</b> {item['price']} €\n"
                        f"🔗 <a href='{item['url']}'>Vedi su Vinted</a>"
                    )
                    
                    try:
                        if item['photo'] and 'http' in item['photo']:
                            await update.message.reply_photo(item['photo'], caption=caption, parse_mode='HTML')
                        else:
                            await update.message.reply_text(caption, parse_mode='HTML')
                    except:
                        await update.message.reply_text(caption, parse_mode='HTML')
                    
                    await asyncio.sleep(0.5)
            
            # Pulisci i dati temporanei
            context.user_data.clear()
            return
            
        except ValueError:
            await update.message.reply_text("❌ Devi inviare un numero!")
            return
    
    # Gestione normale del link
    if 'vinted' in text.lower():
        parts = text.split(' ', 1)
        url = parts[0]
        name = parts[1] if len(parts) > 1 else "Ricerca"
        
        # Salva temporaneamente
        context.user_data['temp_url'] = url
        context.user_data['temp_name'] = name
        context.user_data['awaiting_interval'] = True
        
        # Chiedi l'intervallo
        keyboard = [
            [InlineKeyboardButton("⚡ 1 min", callback_data='interval_1'),
             InlineKeyboardButton("⏱️ 3 min", callback_data='interval_3')],
            [InlineKeyboardButton("🕐 5 min", callback_data='interval_5'),
             InlineKeyboardButton("🕐 10 min", callback_data='interval_10')],
            [InlineKeyboardButton("🕐 30 min", callback_data='interval_30'),
             InlineKeyboardButton("🕐 60 min", callback_data='interval_60')]
        ]
        
        await update.message.reply_text(
            f"✅ <b>Link riconosciuto!</b>\n\n"
            f"🏷️ Nome: {name}\n\n"
            f"⏱️ <b>Ogni quanto vuoi controllare?</b>\n"
            f"(Scegli dai pulsanti o scrivi un numero da 1 a 60)",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'cancel':
        await query.edit_message_text("❌ <b>Operazione annullata</b>", parse_mode='HTML')
        context.user_data.clear()
        
    elif query.data.startswith('interval_'):
        # Gestisci selezione intervallo
        interval = int(query.data.replace('interval_', ''))
        
        url = context.user_data.get('temp_url')
        name = context.user_data.get('temp_name')
        
        await query.edit_message_text("🔍 <b>Verifico il link...</b>", parse_mode='HTML')
        
        items = monitor.fetch_vinted_items(url)
        link_id = monitor.add_user_link(query.from_user.id, url, name, interval * 60)
        
        await query.edit_message_text(
            f"✅ <b>Link aggiunto con successo!</b>\n\n"
            f"🏷️ <b>Nome:</b> {name}\n"
            f"🆔 <b>ID:</b> #{link_id}\n"
            f"📦 <b>Articoli:</b> {len(items)}\n"
            f"⏱️ <b>Controllo ogni:</b> {interval} minuti\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔔 Ti avviserò per nuovi articoli!",
            parse_mode='HTML'
        )
        
        if items:
            for i, item in enumerate(items[:3], 1):
                caption = (
                    f"<b>{item['title']}</b>\n\n"
                    f"💰 <b>Prezzo:</b> {item['price']} €\n"
                    f"🔗 <a href='{item['url']}'>Vedi</a>"
                )
                
                try:
                    if item['photo'] and 'http' in item['photo']:
                        await query.message.reply_photo(item['photo'], caption=caption, parse_mode='HTML')
                    else:
                        await query.message.reply_text(caption, parse_mode='HTML')
                except:
                    await query.message.reply_text(caption, parse_mode='HTML')
                
                await asyncio.sleep(0.5)
        
        context.user_data.clear()
        
    elif query.data.startswith('remove_'):
        lid = query.data.replace('remove_', '')
        if monitor.remove_user_link(query.from_user.id, lid):
            await query.edit_message_text(
                f"✅ <b>Link #{lid} rimosso!</b>\n\n"
                f"Usa /lista per vedere i rimanenti",
                parse_mode='HTML'
            )

async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    logger.info("━━━━━━ 🔍 CONTROLLO PERIODICO ━━━━━━")
    current_time = datetime.now()
    
    for uid, udata in monitor.data['users'].items():
        for lid, ldata in udata['links'].items():
            try:
                # Controlla se è ora di fare il check per questo link
                interval = ldata.get('check_interval', 180)
                last_check = ldata.get('last_check')
                
                if last_check:
                    last_dt = datetime.fromisoformat(last_check)
                    seconds_passed = (current_time - last_dt).total_seconds()
                    
                    if seconds_passed < interval:
                        continue  # Non ancora tempo
                
                new = monitor.check_new_items(uid, lid)
                
                for item in new:
                    caption = (
                        f"🆕 <b>NUOVO ARTICOLO TROVATO!</b>\n\n"
                        f"<b>{item['title']}</b>\n\n"
                        f"💰 <b>Prezzo:</b> {item['price']} €\n"
                        f"🔗 <a href='{item['url']}'>Vedi su Vinted</a>\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📋 Ricerca: <i>{ldata['name']}</i>"
                    )
                    
                    try:
                        if item['photo'] and 'http' in item['photo']:
                            await context.bot.send_photo(
                                int(uid),
                                item['photo'],
                                caption=caption,
                                parse_mode='HTML'
                            )
                        else:
                            await context.bot.send_message(int(uid), caption, parse_mode='HTML')
                        
                        logger.info(f"✅ Notifica inviata a {uid}")
                    except Exception as e:
                        logger.error(f"❌ Errore notifica: {e}")
                
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"❌ Errore check: {e}")
    
    logger.info("━━━━━━ ✅ CONTROLLO COMPLETATO ━━━━━━\n")

def main():
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    if not TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN mancante!")
        return
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("aggiungi", aggiungi))
    app.add_handler(CommandHandler("lista", lista))
    app.add_handler(CommandHandler("test", test_link))
    app.add_handler(CommandHandler("rimuovi", rimuovi))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Controllo ogni 1 MINUTO (il bot controllerà ogni link in base al suo intervallo)
    app.job_queue.run_repeating(check_updates, interval=60, first=10)
    
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("🚀 BOT VINTED AVVIATO!")
    logger.info("⏱️  Controllo personalizzato per ogni link")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
