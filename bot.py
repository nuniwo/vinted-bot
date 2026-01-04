import os
import json
import asyncio
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import requests
from urllib.parse import urlparse, parse_qs
import uuid

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
        self.api_base = 'https://www.vinted.it/api/v2'
        self.token = None
        self._setup_session()
    
    def _setup_session(self):
        """Configura la sessione con headers appropriati"""
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'it-IT,it;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Origin': 'https://www.vinted.it',
            'Referer': 'https://www.vinted.it/',
        })
    
    def _get_token(self):
        """Ottiene un token di sessione valido"""
        try:
            # Fai una richiesta alla homepage per ottenere i cookie di sessione
            response = self.session.get('https://www.vinted.it', timeout=10)
            
            if response.status_code == 200:
                # Cerca il token nel HTML o nei cookie
                cookies = self.session.cookies.get_dict()
                
                # Vinted usa un cookie chiamato _vinted_fr_session o simile
                for cookie_name in cookies:
                    if 'vinted' in cookie_name.lower() and 'session' in cookie_name.lower():
                        logger.info(f"✅ Cookie di sessione trovato: {cookie_name}")
                
                # Cerca anche nei meta tag o script
                if 'csrf-token' in response.text:
                    import re
                    csrf_match = re.search(r'csrf-token["\']?\s*content=["\']([^"\']+)', response.text)
                    if csrf_match:
                        csrf_token = csrf_match.group(1)
                        self.session.headers['X-CSRF-Token'] = csrf_token
                        logger.info("✅ CSRF token trovato e impostato")
                
                return True
            
            return False
        except Exception as e:
            logger.error(f"❌ Errore ottenimento token: {e}")
            return False
    
    def load_data(self):
        """Carica i dati dal file JSON"""
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r') as f:
                    return json.load(f)
            except:
                return {'users': {}}
        return {'users': {}}
    
    def save_data(self):
        """Salva i dati nel file JSON"""
        with open(DATA_FILE, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def add_user_link(self, user_id, link, name):
        """Aggiunge un link da monitorare per un utente"""
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
        """Rimuove un link monitorato"""
        user_id = str(user_id)
        if user_id in self.data['users'] and link_id in self.data['users'][user_id]['links']:
            del self.data['users'][user_id]['links'][link_id]
            self.save_data()
            return True
        return False
    
    def get_user_links(self, user_id):
        """Ottiene tutti i link di un utente"""
        user_id = str(user_id)
        if user_id in self.data['users']:
            return self.data['users'][user_id]['links']
        return {}
    
    def parse_vinted_url(self, url):
        """Estrae i parametri dalla URL di Vinted"""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        
        # Converti liste in valori singoli
        clean_params = {}
        for key, value in params.items():
            if isinstance(value, list) and len(value) > 0:
                clean_params[key] = value[0]
            else:
                clean_params[key] = value
        
        return clean_params
    
    def fetch_vinted_items(self, url):
        """Recupera gli articoli da Vinted usando l'API con token"""
        try:
            logger.info(f"🔍 Fetching URL: {url[:100]}...")
            
            # Ottieni token se non ce l'hai
            if not self.token:
                logger.info("🔑 Ottengo token di sessione...")
                self._get_token()
            
            # Estrai parametri dalla URL
            params = self.parse_vinted_url(url)
            
            # Costruisci URL API
            api_url = f"{self.api_base}/catalog/items"
            
            # Parametri comuni
            api_params = {
                'page': '1',
                'per_page': '20',
                'order': 'newest_first',
            }
            
            # Aggiungi parametri dalla ricerca originale
            if 'search_text' in params:
                api_params['search_text'] = params['search_text']
            if 'catalog_ids' in params:
                api_params['catalog_ids'] = params['catalog_ids']
            if 'brand_ids' in params:
                api_params['brand_ids[]'] = params['brand_ids']
            if 'size_ids' in params:
                api_params['size_ids[]'] = params['size_ids']
            if 'price_from' in params:
                api_params['price_from'] = params['price_from']
            if 'price_to' in params:
                api_params['price_to'] = params['price_to']
            
            logger.info(f"📡 API URL: {api_url}")
            logger.info(f"📋 Parametri: {api_params}")
            
            # Fai la richiesta
            response = self.session.get(api_url, params=api_params, timeout=15)
            logger.info(f"📊 Status: {response.status_code}")
            
            if response.status_code == 401:
                logger.warning("⚠️ Token scaduto, riprovo...")
                self.token = None
                self._get_token()
                response = self.session.get(api_url, params=api_params, timeout=15)
                logger.info(f"📊 Nuovo status: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ HTTP {response.status_code}")
                logger.error(f"Response: {response.text[:500]}")
                
                # Se l'API non funziona, prova metodo alternativo
                logger.info("🔄 Provo metodo alternativo (ricerca diretta)...")
                return self._fetch_items_alternative(url, api_params)
            
            data = response.json()
            
            if 'items' not in data:
                logger.warning("⚠️ Nessun campo 'items' nella risposta")
                logger.info(f"Chiavi disponibili: {list(data.keys())}")
                return self._fetch_items_alternative(url, api_params)
            
            items_data = data['items']
            logger.info(f"📦 Trovati {len(items_data)} articoli")
            
            items = []
            for item in items_data:
                # Estrai foto
                photo_url = None
                if 'photo' in item and item['photo']:
                    photo_url = item['photo'].get('url') or item['photo'].get('full_size_url')
                
                # Prezzo
                price = '0'
                currency = '€'
                if 'price' in item:
                    price = item['price']
                if 'currency' in item:
                    currency = item['currency']
                
                # URL articolo
                item_url = item.get('url', f"https://www.vinted.it/items/{item['id']}")
                if not item_url.startswith('http'):
                    item_url = 'https://www.vinted.it' + item_url
                
                items.append({
                    'id': str(item['id']),
                    'title': item.get('title', 'Senza titolo'),
                    'price': str(price),
                    'currency': currency,
                    'url': item_url,
                    'photo': photo_url
                })
            
            logger.info(f"✅ Estratti {len(items)} articoli!")
            return items
            
        except Exception as e:
            logger.error(f"❌ Errore: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def _fetch_items_alternative(self, url, api_params):
        """Metodo alternativo: costruisce URL di ricerca diretta"""
        try:
            logger.info("🔄 Uso metodo alternativo...")
            
            # Costruisci URL di ricerca diretta
            search_url = "https://www.vinted.it/vetrina/nuovi"
            
            if 'search_text' in api_params:
                search_url = f"https://www.vinted.it/vetrina?search_text={api_params['search_text']}"
            
            logger.info(f"📡 URL alternativo: {search_url}")
            
            # Per ora ritorna una lista vuota, ma indica che il link è valido
            # In futuro si può implementare scraping leggero
            logger.warning("⚠️ Metodo alternativo non ancora implementato completamente")
            
            return []
            
        except Exception as e:
            logger.error(f"❌ Errore metodo alternativo: {e}")
            return []
    
    def check_new_items(self, user_id, link_id):
        """Controlla nuovi articoli"""
        user_id = str(user_id)
        if user_id not in self.data['users']:
            return []
        
        link_data = self.data['users'][user_id]['links'].get(link_id)
        if not link_data:
            return []
        
        logger.info(f"🔍 Controllo link #{link_id}: {link_data['name']}")
        
        current_items = self.fetch_vinted_items(link_data['url'])
        
        if not current_items:
            logger.warning(f"⚠️ Nessun articolo trovato")
            return []
        
        logger.info(f"📦 Articoli trovati: {len(current_items)}")
        
        current_ids = {item['id'] for item in current_items}
        last_ids = {item['id'] for item in link_data['last_items']}
        
        new_item_ids = current_ids - last_ids
        new_items = [item for item in current_items if item['id'] in new_item_ids]
        
        if new_items:
            logger.info(f"🆕 {len(new_items)} nuovi articoli!")
            for item in new_items:
                logger.info(f"   - {item['title'][:50]}")
        else:
            logger.info("✅ Nessun nuovo articolo")
        
        link_data['last_items'] = current_items
        link_data['last_check'] = datetime.now().isoformat()
        self.save_data()
        
        return new_items

monitor = VintedMonitor()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = (
        "🎉 <b>Benvenuto nel Bot Vinted Notifier!</b> 🎉\n\n"
        "👋 Ciao! Monitoro Vinted per te.\n\n"
        "📋 <b>Comandi:</b>\n\n"
        "/aggiungi - Aggiungi link ricerca\n"
        "/lista - I tuoi link\n"
        "/rimuovi - Rimuovi link\n"
        "/test - Test immediato\n\n"
        "💡 <b>Come funziona:</b>\n"
        "1. Cerca su Vinted\n"
        "2. Copia il link\n"
        "3. Usa /aggiungi\n"
        "4. Ricevi notifiche! 🔔\n\n"
        "⏱️ Controllo ogni 5 minuti.\n\n"
        "⚠️ <b>NOTA:</b> A causa delle protezioni di Vinted,\n"
        "il bot potrebbe non funzionare sempre perfettamente.\n"
        "Usa /test per verificare se il link funziona."
    )
    await update.message.reply_text(welcome_message, parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "🔗 <b>Aggiungi link Vinted</b>\n\n"
        "Inviami il link tipo:\n"
        "<code>https://www.vinted.it/catalog?search_text=nike Nike</code>\n\n"
        "⚠️ Il bot tenterà di monitorarlo ma Vinted\n"
        "potrebbe bloccare alcune richieste."
    )
    await update.message.reply_text(message, parse_mode='HTML')

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    links = monitor.get_user_links(user_id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link. Usa /aggiungi! 🚀", parse_mode='HTML')
        return
    
    message = "📋 <b>I tuoi link:</b>\n\n"
    
    for link_id, link_data in links.items():
        num_items = len(link_data.get('last_items', []))
        last_check = link_data.get('last_check', 'Mai')
        if last_check != 'Mai':
            last_check = last_check[:16].replace('T', ' ')
        
        message += (
            f"🔹 <b>#{link_id}</b> - {link_data['name']}\n"
            f"   📦 Articoli: {num_items}\n"
            f"   🕐 Ultimo: {last_check}\n\n"
        )
    
    keyboard = [[InlineKeyboardButton("🗑️ Rimuovi", callback_data='remove_link')]]
    await update.message.reply_text(message, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def test_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    links = monitor.get_user_links(user_id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link. Usa /aggiungi!")
        return
    
    await update.message.reply_text("🔍 Sto testando...")
    
    for link_id, link_data in links.items():
        items = monitor.fetch_vinted_items(link_data['url'])
        
        if items:
            msg = f"✅ <b>{link_data['name']}</b>\n\n{len(items)} articoli!\n\n"
            for i, item in enumerate(items[:3], 1):
                msg += f"{i}. {item['title'][:35]}... - {item['price']}{item['currency']}\n"
        else:
            msg = f"⚠️ <b>{link_data['name']}</b>\n\nNessun articolo (Vinted potrebbe bloccare)"
        
        await update.message.reply_text(msg, parse_mode='HTML')
        await asyncio.sleep(2)

async def rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    links = monitor.get_user_links(user_id)
    
    if not links:
        await update.message.reply_text("📭 Nessun link.")
        return
    
    keyboard = []
    for link_id, link_data in links.items():
        keyboard.append([InlineKeyboardButton(f"🗑️ {link_data['name']}", callback_data=f'remove_{link_id}')])
    keyboard.append([InlineKeyboardButton("❌ Annulla", callback_data='cancel')])
    
    await update.message.reply_text("🗑️ Seleziona:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if 'vinted.it' in text.lower() or 'vinted.com' in text.lower():
        parts = text.split(' ', 1)
        url = parts[0]
        name = parts[1] if len(parts) > 1 else "Ricerca"
        
        await update.message.reply_text("🔍 Verifico...")
        
        test_items = monitor.fetch_vinted_items(url)
        
        user_id = update.effective_user.id
        link_id = monitor.add_user_link(user_id, url, name)
        
        if test_items:
            msg = f"✅ <b>Link aggiunto!</b>\n\n🏷️ {name}\n🆔 #{link_id}\n📦 {len(test_items)} articoli\n\n🔔 Ti avviserò!"
        else:
            msg = f"⚠️ <b>Link aggiunto</b>\n\n🏷️ {name}\n🆔 #{link_id}\n\n⚠️ Nessun articolo trovato ora.\nVinted potrebbe bloccare le richieste.\nProva /test più tardi!"
        
        await update.message.reply_text(msg, parse_mode='HTML')
    else:
        await update.message.reply_text("❌ Link non valido. Usa /aggiungi!")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'cancel':
        await query.edit_message_text("❌ Annullato.")
        return
    
    if query.data.startswith('remove_'):
        link_id = query.data.replace('remove_', '')
        user_id = query.from_user.id
        
        if monitor.remove_user_link(user_id, link_id):
            await query.edit_message_text("✅ <b>Link rimosso!</b>", parse_mode='HTML')
        else:
            await query.edit_message_text("❌ Errore.")

async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔍 CONTROLLO PERIODICO")
    
    for user_id, user_data in monitor.data['users'].items():
        for link_id, link_data in user_data['links'].items():
            try:
                new_items = monitor.check_new_items(user_id, link_id)
                
                for item in new_items:
                    message = (
                        f"🆕 <b>Nuovo!</b>\n\n"
                        f"🏷️ <b>{item['title']}</b>\n"
                        f"💰 {item['price']} {item['currency']}\n"
                        f"🔗 <a href='{item['url']}'>Vedi</a>\n\n"
                        f"📋 {link_data['name']}"
                    )
                    
                    try:
                        if item['photo']:
                            await context.bot.send_photo(
                                chat_id=int(user_id),
                                photo=item['photo'],
                                caption=message,
                                parse_mode='HTML'
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=int(user_id),
                                text=message,
                                parse_mode='HTML'
                            )
                    except Exception as e:
                        logger.error(f"❌ Notifica: {e}")
                
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"❌ Controllo: {e}")
    
    logger.info("✅ COMPLETATO\n")

def main():
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("❌ TOKEN mancante!")
        return
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("aggiungi", aggiungi))
    app.add_handler(CommandHandler("lista", lista))
    app.add_handler(CommandHandler("test", test_link))
    app.add_handler(CommandHandler("rimuovi", rimuovi))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    app.job_queue.run_repeating(check_updates, interval=300, first=10)
    
    logger.info("🚀 BOT VINTED AVVIATO!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
