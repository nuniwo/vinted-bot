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
        # Rimuovi spazi
        url = url.strip()
        
        # Assicurati che abbia https://
        if not url.startswith('http'):
            url = 'https://' + url
        
        # Assicurati che sia www.vinted.it
        url = url.replace('vinted.it/', 'www.vinted.it/')
        url = url.replace('https://vinted.it', 'https://www.vinted.it')
        
        return url
    
    def fetch_vinted_items(self, url):
        """Recupera gli articoli da Vinted tramite scraping HTML del link esatto fornito dall'utente"""
        try:
            # Normalizza l'URL
            url = self.normalize_url(url)
            
            logger.info(f"🔍 Fetching esatto URL utente: {url[:150]}...")
            
            # Fetch della pagina HTML esatta
            response = self.session.get(url, timeout=20, allow_redirects=True)
            
            logger.info(f"📊 Status code: {response.status_code}")
            logger.info(f"📍 URL finale: {response.url[:150]}")
            
            if response.status_code != 200:
                logger.error(f"❌ HTTP {response.status_code}")
                return []
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Cerca script con dati JSON
            items = []
            scripts = soup.find_all('script')
            
            for script in scripts:
                if not script.string:
                    continue
                
                # Cerca diversi pattern di JSON embedded
                patterns = [
                    r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
                    r'window\.__PRELOADED_STATE__\s*=\s*({.*?});',
                    r'"items"\s*:\s*(\[.*?\])',
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, script.string, re.DOTALL)
                    
                    for match in matches:
                        try:
                            # Prova a parsare il JSON
                            if match.startswith('{'):
                                data = json.loads(match)
                                
                                # Cerca gli items in diverse strutture possibili
                                items_data = None
                                
                                # Struttura 1: catalog.items
                                if 'catalog' in data and 'items' in data['catalog']:
                                    items_data = data['catalog']['items']
                                    logger.info(f"✅ Trovati items in catalog.items")
                                
                                # Struttura 2: items diretti
                                elif 'items' in data:
                                    items_data = data['items']
                                    logger.info(f"✅ Trovati items diretti")
                                
                                # Struttura 3: search.items
                                elif 'search' in data and 'items' in data['search']:
                                    items_data = data['search']['items']
                                    logger.info(f"✅ Trovati items in search.items")
                                
                                if items_data and isinstance(items_data, list):
                                    for item in items_data:
                                        if not isinstance(item, dict):
                                            continue
                                        
                                        item_id = item.get('id')
                                        if not item_id:
                                            continue
                                        
                                        # Estrai foto
                                        photo_url = None
                                        if 'photo' in item and item['photo']:
                                            if isinstance(item['photo'], dict):
                                                photo_url = item['photo'].get('url') or item['photo'].get('full_size_url')
                                            elif isinstance(item['photo'], str):
                                                photo_url = item['photo']
                                        
                                        if 'photos' in item and item['photos'] and len(item['photos']) > 0:
                                            first_photo = item['photos'][0]
                                            if isinstance(first_photo, dict):
                                                photo_url = first_photo.get('url') or first_photo.get('full_size_url')
                                        
                                        # Costruisci URL articolo
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
                                        logger.info(f"✅ Estratti {len(items)} articoli dal JSON")
                                        return items[:20]  # Max 20 articoli
                            
                            elif match.startswith('['):
                                # Array diretto di items
                                items_data = json.loads(match)
                                if isinstance(items_data, list):
                                    for item in items_data:
                                        if not isinstance(item, dict):
                                            continue
                                        
                                        item_id = item.get('id')
                                        if not item_id:
                                            continue
                                        
                                        photo_url = None
                                        if 'photo' in item and item['photo']:
                                            if isinstance(item['photo'], dict):
                                                photo_url = item['photo'].get('url')
                                        
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
                                        logger.info(f"✅ Estratti {len(items)} articoli dall'array")
                                        return items[:20]
                        
                        except json.JSONDecodeError as e:
                            continue
                        except Exception as e:
                            logger.error(f"⚠️ Errore parsing JSON: {e}")
                            continue
            
            # Se non trova JSON, prova a cercare elementi HTML
            if not items:
                logger.info("⚠️ JSON non trovato, provo parsing HTML...")
                item_cards = soup.find_all('div', class_=re.compile(r'feed-grid|item-box|ItemBox'))
                logger.info(f"📦 Trovati {len(item_cards)} elementi HTML potenziali")
            
            if not items:
                logger.warning(f"❌ Nessun articolo estratto dall'URL")
            
            return items
            
        except requests.RequestException as e:
            logger.error(f"❌ Errore di connessione: {e}")
            return []
        except Exception as e:
            logger.error(f"❌ Errore generale: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def check_new_items(self, user_id, link_id):
        """Controlla se ci sono nuovi articoli usando l'URL ESATTO dell'utente"""
        user_id = str(user_id)
        if user_id not in self.data['users']:
            return []
        
        link_data = self.data['users'][user_id]['links'].get(link_id)
        if not link_data:
            return []
        
        logger.info(f"🔍 Controllo link #{link_id}: {link_data['name']}")
        logger.info(f"🔗 URL: {link_data['url'][:150]}")
        
        # Fetch usando l'URL ESATTO salvato dall'utente
        current_items = self.fetch_vinted_items(link_data['url'])
        
        if not current_items:
            logger.warning(f"⚠️ Nessun articolo trovato per link #{link_id}")
            return []
        
        logger.info(f"📦 Articoli trovati nella ricerca: {len(current_items)}")
        
        current_ids = {item['id'] for item in current_items}
        last_ids = {item['id'] for item in link_data['last_items']}
        
        logger.info(f"🆔 IDs attuali: {len(current_ids)}, IDs precedenti: {len(last_ids)}")
        
        # Trova nuovi articoli
        new_item_ids = current_ids - last_ids
        new_items = [item for item in current_items if item['id'] in new_item_ids]
        
        if new_items:
            logger.info(f"🆕 Trovati {len(new_items)} nuovi articoli!")
            for item in new_items:
                logger.info(f"   - {item['title'][:50]} (ID: {item['id']})")
        else:
            logger.info(f"✅ Nessun nuovo articolo per questo link")
        
        # Aggiorna gli ultimi articoli
        link_data['last_items'] = current_items
        link_data['last_check'] = datetime.now().isoformat()
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
        "<code>https://www.vinted.it/catalog?search_text=nike&brand_ids[]=53</code>\n\n"
        "📌 Dopo il link, aggiungi un nome per identificarlo:\n"
        "<code>[LINK] Nome ricerca</code>\n\n"
        "🎯 <b>Esempio completo:</b>\n"
        "<code>https://www.vinted.it/catalog?search_text=nike Nike Scarpe</code>\n\n"
        "⚠️ <b>IMPORTANTE:</b> Copia l'URL COMPLETO dalla barra degli indirizzi del browser!"
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
        last_check = link_data.get('last_check', 'Mai')
        if last_check != 'Mai':
            last_check = last_check[:16].replace('T', ' ')
        
        message += (
            f"🔹 <b>#{link_id}</b> - {link_data['name']}\n"
            f"   📅 Aggiunto: {link_data['added_at'][:10]}\n"
            f"   📦 Articoli tracciati: {num_items}\n"
            f"   🕐 Ultimo controllo: {last_check}\n"
            f"   🔗 <a href='{link_data['url'][:100]}'>Apri su Vinted</a>\n\n"
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
        msg += f"📍 URL: <code>{link_data['url'][:80]}...</code>\n\n"
        
        items = monitor.fetch_vinted_items(link_data['url'])
        
        if items:
            msg += f"✅ Trovati <b>{len(items)}</b> articoli con i tuoi filtri!\n\n"
            msg += "📦 <b>Ultimi 3 articoli:</b>\n"
            for i, item in enumerate(items[:3], 1):
                msg += f"{i}. {item['title'][:40]}... - {item['price']} {item['currency']}\n"
        else:
            msg += "❌ Nessun articolo trovato.\n\n"
            msg += "Possibili cause:\n"
            msg += "• Il link non è corretto\n"
            msg += "• La ricerca non ha risultati\n"
            msg += "• Vinted sta bloccando le richieste\n\n"
            msg += "Prova a copiare di nuovo l'URL completo dalla barra degli indirizzi!"
        
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
        
        # Test immediato del link ESATTO fornito dall'utente
        await update.message.reply_text(
            "🔍 Sto verificando il tuo link esatto...\n\n"
            f"📍 URL: <code>{url[:80]}...</code>\n\n"
            "Attendi...",
            parse_mode='HTML'
        )
        
        test_items = monitor.fetch_vinted_items(url)
        
        if not test_items:
            await update.message.reply_text(
                "❌ <b>Nessun articolo trovato con questo link</b>\n\n"
                "Assicurati di:\n"
                "✅ Copiare l'URL COMPLETO dalla barra degli indirizzi\n"
                "✅ Il link inizi con https://www.vinted.it/catalog?\n"
                "✅ La ricerca su Vinted abbia risultati\n\n"
                "💡 <b>Come fare:</b>\n"
                "1. Apri Vinted nel browser\n"
                "2. Imposta i filtri di ricerca\n"
                "3. Copia TUTTO l'URL dalla barra in alto\n"
                "4. Incollalo qui\n\n"
                "Riprova! 🔄",
                parse_mode='HTML'
            )
            return
        
        # Aggiungi il link ESATTO
        user_id = update.effective_user.id
        link_id = monitor.add_user_link(user_id, url, name)
        
        message = (
            "✅ <b>Link aggiunto con successo!</b>\n\n"
            f"🏷️ <b>Nome:</b> {name}\n"
            f"🆔 <b>ID:</b> #{link_id}\n"
            f"📦 <b>Articoli trovati:</b> {len(test_items)}\n"
            f"📍 <b>URL salvato:</b> <code>{url[:60]}...</code>\n\n"
            "🔔 Riceverai notifiche quando verranno pubblicati nuovi articoli!\n"
            "⏱️ Primo controllo tra circa 5 minuti.\n\n"
            "📋 Usa /lista per vedere i dettagli\n"
            "🔄 Usa /test per verificare subito"
        )
        await update.message.reply_text(message, parse_mode='HTML')
    else:
        await update.message.reply_text(
            "❌ Link non valido.\n\n"
            "Invia un link di ricerca Vinted (deve contenere 'vinted.it').\n"
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
    """Controlla periodicamente nuovi articoli usando gli URL ESATTI degli utenti"""
    logger.info("=" * 80)
    logger.info("🔍 INIZIO CONTROLLO PERIODICO")
    logger.info("=" * 80)
    
    total_users = len(monitor.data['users'])
    total_links = sum(len(user_data['links']) for user_data in monitor.data['users'].values())
    
    logger.info(f"👥 Utenti totali: {total_users}")
    logger.info(f"🔗 Link totali da controllare: {total_links}")
    
    for user_id, user_data in monitor.data['users'].items():
        logger.info(f"\n{'='*60}")
        logger.info(f"👤 Controllo utente {user_id}")
        logger.info(f"{'='*60}")
        
        for link_id, link_data in user_data['links'].items():
            try:
                logger.info(f"\n🔗 Link #{link_id}: {link_data['name']}")
                
                # Usa l'URL ESATTO salvato dall'utente
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
                        logger.info(f"✅ Notifica inviata a utente {user_id}")
                    except Exception as e:
                        logger.error(f"❌ Errore invio notifica: {e}")
                
                # Pausa tra i controlli per non sovraccaricare
                await asyncio.sleep(5)
                
            except Exception as e:
                logger.error(f"❌ Errore controllo link {link_id}: {e}")
                import traceback
                logger.error(traceback.format_exc())
    
    logger.info("\n" + "=" * 80)
    logger.info("✅ CONTROLLO PERIODICO COMPLETATO")
    logger.info("=" * 80 + "\n")

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
    
    logger.info("=" * 80)
    logger.info("🚀 BOT AVVIATO CON SUCCESSO!")
    logger.info("=" * 80)
    
    # Avvia il bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
