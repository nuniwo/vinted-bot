import os
import json
import time
import asyncio
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urlparse, parse_qs, urlencode

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
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
    
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
    
    def normalize_url(self, url):
        """Normalizza l'URL per assicurarsi che sia corretto"""
        url = url.strip()
        if not url.startswith('http'):
            url = 'https://' + url
        if 'vinted.it' in url and 'www.vinted.it' not in url:
            url = url.replace('vinted.it', 'www.vinted.it')
        url = url.replace('www.www.', 'www.')
        return url
    
    def find_items_recursive(self, obj, depth=0, max_depth=10):
        """Cerca ricorsivamente un array 'items' nel JSON"""
        if depth > max_depth:
            return None
        
        if isinstance(obj, dict):
            if 'items' in obj and isinstance(obj['items'], list):
                if len(obj['items']) > 0 and isinstance(obj['items'][0], dict):
                    if 'id' in obj['items'][0] and 'title' in obj['items'][0]:
                        return obj['items']
            for value in obj.values():
                result = self.find_items_recursive(value, depth + 1, max_depth)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = self.find_items_recursive(item, depth + 1, max_depth)
                if result:
                    return result
        return None
    
    def fetch_vinted_items(self, url):
        """Recupera gli articoli da Vinted tramite scraping HTML"""
        try:
            url = self.normalize_url(url)
            logger.info(f"🔍 Fetching URL: {url[:150]}...")
            
            response = self.session.get(url, timeout=20, allow_redirects=True)
            logger.info(f"📊 Status: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ HTTP {response.status_code}")
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            items = []
            scripts = soup.find_all('script')
            logger.info(f"📜 Trovati {len(scripts)} script tags")
            
            for idx, script in enumerate(scripts):
                if not script.string:
                    continue
                
                script_content = script.string
                
                if 'items' in script_content or 'catalog' in script_content:
                    logger.info(f"🎯 Script #{idx} potenzialmente utile")
                
                patterns = [
                    (r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\});', 'INITIAL_STATE'),
                    (r'window\.__PRELOADED_STATE__\s*=\s*(\{.+?\});', 'PRELOADED_STATE'),
                ]
                
                for pattern, pattern_name in patterns:
                    matches = re.findall(pattern, script_content, re.DOTALL)
                    
                    if matches:
                        logger.info(f"✅ Pattern '{pattern_name}' trovato!")
                    
                    for match in matches:
                        try:
                            json_str = match.strip()
                            if json_str.endswith(';'):
                                json_str = json_str[:-1]
                            
                            data = json.loads(json_str)
                            logger.info(f"📦 JSON parsato! Chiavi top: {list(data.keys())[:5]}")
                            
                            items_data = None
                            
                            if 'catalog' in data and isinstance(data['catalog'], dict):
                                if 'items' in data['catalog']:
                                    items_data = data['catalog']['items']
                                    logger.info(f"✅ Trovati items in catalog.items")
                            
                            if not items_data and 'items' in data:
                                items_data = data['items']
                                logger.info(f"✅ Trovati items diretti")
                            
                            if not items_data:
                                logger.info("🔍 Cerco items ricorsivamente...")
                                items_data = self.find_items_recursive(data)
                                if items_data:
                                    logger.info(f"✅ Trovati items ricorsivamente!")
                            
                            if items_data and isinstance(items_data, list) and len(items_data) > 0:
                                logger.info(f"📦 Processando {len(items_data)} items...")
                                for item in items_data:
                                    if not isinstance(item, dict):
                                        continue
                                    
                                    item_id = item.get('id')
                                    if not item_id:
                                        continue
                                    
                                    photo_url = None
                                    if 'photo' in item and item['photo']:
                                        if isinstance(item['photo'], dict):
                                            photo_url = item['photo'].get('url') or item['photo'].get('full_size_url')
                                    
                                    if 'photos' in item and item['photos'] and len(item['photos']) > 0:
                                        first_photo = item['photos'][0]
                                        if isinstance(first_photo, dict):
                                            photo_url = first_photo.get('url') or first_photo.get('full_size_url')
                                    
                                    item_url = item.get('url', f"https://www.vinted.it/items/{item_id}")
                                    if not item_url.startswith('http'):
                                        item_url = 'https://www.vinted.it' + item_url
                                    
                                    items.append({
                                        'id': str(item_id),
                                        'title': item.get('title', 'Senza titolo'),
                                        'price': str(item.get('price', '0')),
                                        'currency': item.get('currency', '€'),
                                        'url': item_url,
                                        'photo': photo_url
                                    })
                                
                                if items:
                                    logger.info(f"✅ Estratti {len(items)} articoli!")
                                    return items[:20]
                        
                        except json.JSONDecodeError as e:
                            logger.warning(f"⚠️ JSON decode error: {str(e)[:100]}")
                            continue
                        except Exception as e:
                            logger.error(f"⚠️ Errore parsing: {str(e)[:100]}")
                            continue
            
            if not items:
                logger.warning("❌ Nessun articolo estratto")
            
            return items
            
        except requests.RequestException as e:
            logger.error(f"❌ Errore connessione: {str(e)[:200]}")
            return []
        except Exception as e:
            logger.error(f"❌ Errore generale: {str(e)[:200]}")
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
        "👋 Ciao! Sono qui per aiutarti a monitorare i tuoi articoli preferiti su Vinted.\n\n"
        "📋 <b>Comandi disponibili:</b>\n\n"
        "🔗 /aggiungi - Aggiungi un nuovo link di ricerca Vinted\n"
        "📜 /lista - Visualizza tutti i tuoi link monitorati\n"
        "🗑️ /rimuovi - Rimuovi un link dalla lista\n"
        "🔄 /test - Testa immediatamente un link\n"
        "ℹ️ /help - Mostra questo messaggio di aiuto\n\n"
        "💡 <b>Come funziona:</b>\n"
        "1️⃣ Vai su Vinted e imposta i tuoi filtri di ricerca\n"
        "2️⃣ Copia il link della ricerca\n"
        "3️⃣ Usa /aggiungi per registrarlo\n"
        "4️⃣ Riceverai notifiche per ogni nuovo articolo! 🔔\n\n"
        "⏱️ Il bot controlla ogni 5 minuti automaticamente."
    )
    await update.message.reply_text(welcome_message, parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "🔗 <b>Aggiungi un nuovo link Vinted</b>\n\n"
        "📝 Inviami il link di ricerca Vinted che vuoi monitorare.\n\n"
        "💡 <b>Esempio:</b>\n"
        "<code>https://www.vinted.it/catalog?search_text=nike</code>\n\n"
        "📌 Dopo il link, aggiungi un nome:\n"
        "<code>[LINK] Nome ricerca</code>\n\n"
        "🎯 <b>Esempio completo:</b>\n"
        "<code>https://www.vinted.it/catalog?search_text=nike Nike Scarpe</code>"
    )
    await update.message.reply_text(message, parse_mode='HTML')

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    links = monitor.get_user_links(user_id)
    
    if not links:
        message = (
            "📭 <b>Nessun link monitorato</b>\n\n"
            "Non hai ancora aggiunto nessun link da monitorare.\n\n"
            "Usa /aggiungi per iniziare! 🚀"
        )
        await update.message.reply_text(message, parse_mode='HTML')
        return
    
    message = "📋 <b>I tuoi link monitorati:</b>\n\n"
    
    for link_id, link_data in links.items():
        num_items = len(link_data.get('last_items', []))
        last_check = link_data.get('last_check', 'Mai')
        if last_check != 'Mai':
            last_check = last_check[:16].replace('T', ' ')
        
        message += (
            f"🔹 <b>#{link_id}</b> - {link_data['name']}\n"
            f"   📅 Aggiunto: {link_data['added_at'][:10]}\n"
            f"   📦 Articoli: {num_items}\n"
            f"   🕐 Ultimo controllo: {last_check}\n\n"
        )
    
    keyboard = [[InlineKeyboardButton("🗑️ Rimuovi", callback_data='remove_link')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, parse_mode='HTML', reply_markup=reply_markup)

async def test_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    links = monitor.get_user_links(user_id)
    
    if not links:
        await update.message.reply_text("📭 Non hai link da testare.\n\nUsa /aggiungi! 🔗")
        return
    
    await update.message.reply_text("🔍 Sto testando i tuoi link...")
    
    for link_id, link_data in links.items():
        msg = f"🔗 <b>Link #{link_id}: {link_data['name']}</b>\n\n"
        
        items = monitor.fetch_vinted_items(link_data['url'])
        
        if items:
            msg += f"✅ Trovati <b>{len(items)}</b> articoli!\n\n"
            msg += "📦 <b>Ultimi 3:</b>\n"
            for i, item in enumerate(items[:3], 1):
                msg += f"{i}. {item['title'][:40]}... - {item['price']} {item['currency']}\n"
        else:
            msg += "❌ Nessun articolo trovato"
        
        await update.message.reply_text(msg, parse_mode='HTML')

async def rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    links = monitor.get_user_links(user_id)
    
    if not links:
        await update.message.reply_text("📭 Non hai link da rimuovere.")
        return
    
    keyboard = []
    for link_id, link_data in links.items():
        keyboard.append([InlineKeyboardButton(
            f"🗑️ {link_data['name']}", 
            callback_data=f'remove_{link_id}'
        )])
    
    keyboard.append([InlineKeyboardButton("❌ Annulla", callback_data='cancel')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🗑️ <b>Seleziona il link da rimuovere:</b>",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if 'vinted.it' in text.lower() or 'vinted.com' in text.lower():
        parts = text.split(' ', 1)
        url = parts[0]
        name = parts[1] if len(parts) > 1 else "Ricerca senza nome"
        
        await update.message.reply_text("🔍 Verifico il link...")
        
        test_items = monitor.fetch_vinted_items(url)
        
        if not test_items:
            await update.message.reply_text(
                "❌ <b>Nessun articolo trovato</b>\n\n"
                "Assicurati di copiare l'URL completo!",
                parse_mode='HTML'
            )
            return
        
        user_id = update.effective_user.id
        link_id = monitor.add_user_link(user_id, url, name)
        
        message = (
            "✅ <b>Link aggiunto con successo!</b>\n\n"
            f"🏷️ <b>Nome:</b> {name}\n"
            f"🆔 <b>ID:</b> #{link_id}\n"
            f"📦 <b>Articoli trovati:</b> {len(test_items)}\n\n"
            "🔔 Riceverai notifiche per nuovi articoli!\n"
            "⏱️ Primo controllo tra 5 minuti."
        )
        await update.message.reply_text(message, parse_mode='HTML')
    else:
        await update.message.reply_text("❌ Link non valido.\n\nUsa /aggiungi per info! 💡")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'cancel':
        await query.edit_message_text("❌ Operazione annullata.")
        return
    
    if query.data.startswith('remove_'):
        link_id = query.data.replace('remove_', '')
        user_id = query.from_user.id
        
        if monitor.remove_user_link(user_id, link_id):
            await query.edit_message_text(
                "✅ <b>Link rimosso!</b>\n\nUsa /lista per vedere i rimanenti.",
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text("❌ Errore rimozione.")

async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    logger.info("=" * 60)
    logger.info("🔍 CONTROLLO PERIODICO")
    logger.info("=" * 60)
    
    for user_id, user_data in monitor.data['users'].items():
        for link_id, link_data in user_data['links'].items():
            try:
                new_items = monitor.check_new_items(user_id, link_id)
                
                for item in new_items:
                    message = (
                        f"🆕 <b>Nuovo articolo trovato!</b>\n\n"
                        f"🏷️ <b>{item['title']}</b>\n"
                        f"💰 <b>Prezzo:</b> {item['price']} {item['currency']}\n"
                        f"🔗 <a href='{item['url']}'>Vedi su Vinted</a>\n\n"
                        f"📋 Ricerca: <i>{link_data['name']}</i>"
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
                        logger.info(f"✅ Notifica inviata")
                    except Exception as e:
                        logger.error(f"❌ Errore notifica: {e}")
                
                await asyncio.sleep(5)
                
            except Exception as e:
                logger.error(f"❌ Errore controllo: {e}")
    
    logger.info("✅ CONTROLLO COMPLETATO\n")

def main():
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN non impostato!")
        return
    
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("aggiungi", aggiungi))
    application.add_handler(CommandHandler("lista", lista))
    application.add_handler(CommandHandler("test", test_link))
    application.add_handler(CommandHandler("rimuovi", rimuovi))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    job_queue = application.job_queue
    job_queue.run_repeating(check_updates, interval=300, first=10)
    
    logger.info("🚀 BOT AVVIATO!")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
