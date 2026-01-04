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
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://www.vinted.it/',
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
    
    def extract_catalog_id(self, url):
        """Estrae l'ID del catalogo dall'URL"""
        try:
            # Estrai parametri dall'URL
            params = {}
            if '?' in url:
                query_string = url.split('?')[1]
                for param in query_string.split('&'):
                    if '=' in param:
                        key, value = param.split('=', 1)
                        params[key] = value
            return params
        except Exception as e:
            logger.error(f"Errore estrazione parametri: {e}")
            return {}
    
    def fetch_vinted_items(self, url):
        """Recupera gli articoli da Vinted con metodo migliorato"""
        try:
            logger.info(f"🔍 Fetching URL: {url[:100]}...")
            
            # Prova prima con l'API
            items = self.fetch_via_api(url)
            if items:
                logger.info(f"✅ API: Trovati {len(items)} articoli")
                return items
            
            # Se l'API fallisce, prova con scraping HTML
            logger.info("⚠️ API fallita, provo con scraping HTML...")
            items = self.fetch_via_scraping(url)
            if items:
                logger.info(f"✅ Scraping: Trovati {len(items)} articoli")
                return items
            
            logger.warning("❌ Nessun metodo ha funzionato")
            return []
            
        except Exception as e:
            logger.error(f"❌ Errore generale nel fetch: {e}")
            return []
    
    def fetch_via_api(self, url):
        """Prova a recuperare via API"""
        try:
            # Converti URL web in API URL
            if 'catalog?' in url:
                api_url = url.replace('www.vinted.it/catalog?', 'www.vinted.it/api/v2/catalog/items?')
                api_url = api_url.replace('https://vinted.it/catalog?', 'https://www.vinted.it/api/v2/catalog/items?')
                
                # Aggiungi per_page per avere più risultati
                if 'per_page' not in api_url:
                    api_url += '&per_page=20'
                
                logger.info(f"📡 Chiamata API: {api_url[:100]}...")
                
                response = self.session.get(api_url, timeout=15)
                logger.info(f"📊 Status code: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    items = []
                    
                    for item in data.get('items', []):
                        items.append({
                            'id': str(item.get('id')),
                            'title': item.get('title', 'Senza titolo'),
                            'price': item.get('price', '0'),
                            'currency': item.get('currency', '€'),
                            'url': item.get('url', f"https://www.vinted.it/items/{item.get('id')}"),
                            'photo': item.get('photo', {}).get('url') if item.get('photo') else None
                        })
                    
                    return items[:15]  # Limita a 15 articoli
            
            return []
        except Exception as e:
            logger.error(f"❌ Errore API: {e}")
            return []
    
    def fetch_via_scraping(self, url):
        """Recupera via scraping HTML"""
        try:
            response = self.session.get(url, timeout=15)
            
            if response.status_code != 200:
                logger.error(f"❌ HTTP {response.status_code}")
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Cerca i dati JSON embedded nella pagina
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and 'catalog' in script.string:
                    # Cerca pattern JSON
                    json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', script.string, re.DOTALL)
                    if json_match:
                        try:
                            data = json.loads(json_match.group(1))
                            items_data = data.get('catalog', {}).get('items', [])
                            
                            items = []
                            for item in items_data[:15]:
                                items.append({
                                    'id': str(item.get('id')),
                                    'title': item.get('title', 'Senza titolo'),
                                    'price': item.get('price', '0'),
                                    'currency': item.get('currency', '€'),
                                    'url': item.get('url', f"https://www.vinted.it/items/{item.get('id')}"),
                                    'photo': item.get('photo', {}).get('url') if item.get('photo') else None
                                })
                            
                            return items
                        except json.JSONDecodeError:
                            continue
            
            logger.warning("⚠️ Nessun JSON trovato nell'HTML")
            return []
            
        except Exception as e:
            logger.error(f"❌ Errore scraping: {e}")
            return []
    
    def check_new_items(self, user_id, link_id):
        """Controlla se ci sono nuovi articoli"""
        user_id = str(user_id)
        if user_id not in self.data['users']:
            return []
        
        link_data = self.data['users'][user_id]['links'].get(link_id)
        if not link_data:
            return []
        
        logger.info(f"🔍 Controllo link #{link_id}: {link_data['name']}")
        
        current_items = self.fetch_vinted_items(link_data['url'])
        if not current_items:
            logger.warning(f"⚠️ Nessun articolo trovato per link #{link_id}")
            return []
        
        logger.info(f"📦 Articoli attuali: {len(current_items)}")
        
        current_ids = {item['id'] for item in current_items}
        last_ids = {item['id'] for item in link_data['last_items']}
        
        logger.info(f"🆔 IDs attuali: {len(current_ids)}, IDs precedenti: {len(last_ids)}")
        
        # Trova nuovi articoli
        new_item_ids = current_ids - last_ids
        new_items = [item for item in current_items if item['id'] in new_item_ids]
        
        if new_items:
            logger.info(f"🆕 Trovati {len(new_items)} nuovi articoli!")
            for item in new_items:
                logger.info(f"   - {item['title']} (ID: {item['id']})")
        else:
            logger.info(f"✅ Nessun nuovo articolo")
        
        # Aggiorna gli ultimi articoli
        link_data['last_items'] = current_items
        self.save_data()
        
        return new_items

# Inizializza il monitor
monitor = VintedMonitor()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start"""
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
    """Comando /help"""
    await start(update, context)

async def aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /aggiungi"""
    message = (
        "🔗 <b>Aggiungi un nuovo link Vinted</b>\n\n"
        "📝 Inviami il link di ricerca Vinted che vuoi monitorare.\n\n"
        "💡 <b>Esempio:</b>\n"
        "<code>https://www.vinted.it/catalog?search_text=nike&brand_ids[]=...</code>\n\n"
        "📌 Dopo il link, aggiungi un nome per identificarlo:\n"
        "<code>[LINK] Nome ricerca</code>\n\n"
        "🎯 <b>Esempio completo:</b>\n"
        "<code>https://www.vinted.it/catalog?search_text=nike Nike Scarpe</code>"
    )
    await update.message.reply_text(message, parse_mode='HTML')

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /lista"""
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
        message += (
            f"🔹 <b>#{link_id}</b> - {link_data['name']}\n"
            f"   📅 Aggiunto: {link_data['added_at'][:10]}\n"
            f"   📦 Articoli tracciati: {num_items}\n"
            f"   🔗 <a href='{link_data['url']}'>Apri su Vinted</a>\n\n"
        )
    
    keyboard = [[InlineKeyboardButton("🗑️ Rimuovi un link", callback_data='remove_link')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, parse_mode='HTML', reply_markup=reply_markup, disable_web_page_preview=True)

async def test_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /test - Testa immediatamente i link"""
    user_id = update.effective_user.id
    links = monitor.get_user_links(user_id)
    
    if not links:
        await update.message.reply_text(
            "📭 Non hai link da testare.\n\nUsa /aggiungi per aggiungere un link! 🔗"
        )
        return
    
    await update.message.reply_text("🔍 Sto testando i tuoi link...\n\nAttendi qualche secondo...")
    
    for link_id, link_data in links.items():
        msg = f"🔗 <b>Link #{link_id}: {link_data['name']}</b>\n\n"
        
        items = monitor.fetch_vinted_items(link_data['url'])
        
        if items:
            msg += f"✅ Trovati <b>{len(items)}</b> articoli!\n\n"
            msg += "📦 <b>Ultimi 3 articoli:</b>\n"
            for i, item in enumerate(items[:3], 1):
                msg += f"{i}. {item['title'][:40]}... - {item['price']} {item['currency']}\n"
        else:
            msg += "❌ Nessun articolo trovato. Verifica che il link sia corretto."
        
        await update.message.reply_text(msg, parse_mode='HTML')

async def rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /rimuovi"""
    user_id = update.effective_user.id
    links = monitor.get_user_links(user_id)
    
    if not links:
        await update.message.reply_text(
            "📭 Non hai link da rimuovere.\n\nUsa /aggiungi per aggiungere un link! 🔗"
        )
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
    """Gestisce i messaggi con link"""
    text = update.message.text
    
    # Verifica se è un link Vinted
    if 'vinted.it' in text.lower() or 'vinted.com' in text.lower():
        # Estrai link e nome
        parts = text.split(' ', 1)
        url = parts[0]
        name = parts[1] if len(parts) > 1 else "Ricerca senza nome"
        
        # Test immediato del link
        await update.message.reply_text("🔍 Sto verificando il link... Attendi...")
        
        test_items = monitor.fetch_vinted_items(url)
        
        if not test_items:
            await update.message.reply_text(
                "❌ <b>Link non valido o non accessibile</b>\n\n"
                "Il link non restituisce articoli. Verifica che:\n"
                "• Il link sia corretto\n"
                "• La ricerca abbia risultati su Vinted\n"
                "• Il link inizi con https://www.vinted.it/catalog?\n\n"
                "Riprova con un link diverso! 💡",
                parse_mode='HTML'
            )
            return
        
        # Aggiungi il link
        user_id = update.effective_user.id
        link_id = monitor.add_user_link(user_id, url, name)
        
        message = (
            "✅ <b>Link aggiunto con successo!</b>\n\n"
            f"🏷️ <b>Nome:</b> {name}\n"
            f"🆔 <b>ID:</b> #{link_id}\n"
            f"📦 <b>Articoli trovati:</b> {len(test_items)}\n\n"
            f"🔔 Riceverai notifiche quando verranno pubblicati nuovi articoli!\n"
            f"⏱️ Primo controllo tra circa 5 minuti.\n\n"
            "📋 Usa /lista per vedere tutti i tuoi link monitorati.\n"
            "🔄 Usa /test per verificare subito i link."
        )
        await update.message.reply_text(message, parse_mode='HTML')
    else:
        await update.message.reply_text(
            "❌ Link non valido.\n\n"
            "Invia un link di ricerca Vinted valido.\n"
            "Usa /aggiungi per maggiori informazioni! 💡"
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce i callback dei bottoni"""
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
                "✅ <b>Link rimosso con successo!</b>\n\n"
                "Il link non sarà più monitorato.\n\n"
                "Usa /lista per vedere i tuoi link rimanenti. 📋",
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text("❌ Errore nella rimozione del link.")

async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    """Controlla periodicamente nuovi articoli"""
    logger.info("=" * 60)
    logger.info("🔍 INIZIO CONTROLLO PERIODICO")
    logger.info("=" * 60)
    
    total_users = len(monitor.data['users'])
    total_links = sum(len(user_data['links']) for user_data in monitor.data['users'].values())
    
    logger.info(f"👥 Utenti totali: {total_users}")
    logger.info(f"🔗 Link totali da controllare: {total_links}")
    
    for user_id, user_data in monitor.data['users'].items():
        logger.info(f"\n👤 Controllo utente {user_id}")
        
        for link_id, link_data in user_data['links'].items():
            try:
                logger.info(f"\n🔗 Link #{link_id}: {link_data['name']}")
                new_items = monitor.check_new_items(user_id, link_id)
                
                for item in new_items:
                    message = (
                        f"🆕 <b>Nuovo articolo trovato!</b>\n\n"
                        f"🏷️ <b>{item['title']}</b>\n"
                        f"💰 <b>Prezzo:</b> {item['price']} {item['currency']}\n"
                        f"🔗 <a href='{item['url']}'>Visualizza su Vinted</a>\n\n"
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
                                parse_mode='HTML',
                                disable_web_page_preview=False
                            )
                        logger.info(f"✅ Notifica inviata a {user_id}")
                    except Exception as e:
                        logger.error(f"❌ Errore invio notifica: {e}")
                
                # Pausa tra i controlli
                await asyncio.sleep(3)
                
            except Exception as e:
                logger.error(f"❌ Errore controllo link {link_id}: {e}")
    
    logger.info("=" * 60)
    logger.info("✅ CONTROLLO PERIODICO COMPLETATO")
    logger.info("=" * 60)

def main():
    """Avvia il bot"""
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN non impostato!")
        return
    
    # Crea l'applicazione
    application = Application.builder().token(TOKEN).build()
    
    # Aggiungi i gestori
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("aggiungi", aggiungi))
    application.add_handler(CommandHandler("lista", lista))
    application.add_handler(CommandHandler("test", test_link))
    application.add_handler(CommandHandler("rimuovi", rimuovi))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Aggiungi job per controllare aggiornamenti ogni 5 minuti
    job_queue = application.job_queue
    job_queue.run_repeating(check_updates, interval=300, first=10)
    
    logger.info("=" * 60)
    logger.info("🚀 BOT AVVIATO CON SUCCESSO!")
    logger.info("=" * 60)
    
    # Avvia il bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
