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
    
    def fetch_vinted_items(self, url):
        """Recupera gli articoli da Vinted"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            # Estrai i parametri di ricerca dall'URL
            if 'catalog?' in url:
                # Converti URL web in API URL
                api_url = url.replace('www.vinted.it/catalog?', 'www.vinted.it/api/v2/catalog/items?')
                response = requests.get(api_url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    items = []
                    
                    for item in data.get('items', [])[:10]:  # Primi 10 articoli
                        items.append({
                            'id': item.get('id'),
                            'title': item.get('title'),
                            'price': item.get('price'),
                            'currency': item.get('currency'),
                            'url': item.get('url'),
                            'photo': item.get('photo', {}).get('url') if item.get('photo') else None
                        })
                    
                    return items
            
            return []
        except Exception as e:
            logger.error(f"Errore nel fetch di Vinted: {e}")
            return []
    
    def check_new_items(self, user_id, link_id):
        """Controlla se ci sono nuovi articoli"""
        user_id = str(user_id)
        if user_id not in self.data['users']:
            return []
        
        link_data = self.data['users'][user_id]['links'].get(link_id)
        if not link_data:
            return []
        
        current_items = self.fetch_vinted_items(link_data['url'])
        if not current_items:
            return []
        
        current_ids = {item['id'] for item in current_items}
        last_ids = {item['id'] for item in link_data['last_items']}
        
        # Trova nuovi articoli
        new_items = [item for item in current_items if item['id'] not in last_ids]
        
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
        "ℹ️ /help - Mostra questo messaggio di aiuto\n\n"
        "💡 <b>Come funziona:</b>\n"
        "1️⃣ Vai su Vinted e imposta i tuoi filtri di ricerca\n"
        "2️⃣ Copia il link della ricerca\n"
        "3️⃣ Usa /aggiungi per registrarlo\n"
        "4️⃣ Riceverai notifiche per ogni nuovo articolo! 🔔"
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
        message += (
            f"🔹 <b>#{link_id}</b> - {link_data['name']}\n"
            f"   📅 Aggiunto: {link_data['added_at'][:10]}\n"
            f"   🔗 <a href='{link_data['url']}'>Apri su Vinted</a>\n\n"
        )
    
    keyboard = [[InlineKeyboardButton("🗑️ Rimuovi un link", callback_data='remove_link')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, parse_mode='HTML', reply_markup=reply_markup, disable_web_page_preview=True)

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
    if 'vinted.it' in text.lower():
        # Estrai link e nome
        parts = text.split(' ', 1)
        url = parts[0]
        name = parts[1] if len(parts) > 1 else "Ricerca senza nome"
        
        # Aggiungi il link
        user_id = update.effective_user.id
        link_id = monitor.add_user_link(user_id, url, name)
        
        message = (
            "✅ <b>Link aggiunto con successo!</b>\n\n"
            f"🏷️ <b>Nome:</b> {name}\n"
            f"🆔 <b>ID:</b> #{link_id}\n\n"
            "🔔 Riceverai notifiche quando verranno pubblicati nuovi articoli!\n\n"
            "📋 Usa /lista per vedere tutti i tuoi link monitorati."
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
    logger.info("🔍 Controllo nuovi articoli...")
    
    for user_id, user_data in monitor.data['users'].items():
        for link_id, link_data in user_data['links'].items():
            try:
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
                    except Exception as e:
                        logger.error(f"Errore invio notifica: {e}")
                
                # Pausa tra i controlli
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Errore controllo link {link_id}: {e}")

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
    application.add_handler(CommandHandler("rimuovi", rimuovi))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Aggiungi job per controllare aggiornamenti ogni 5 minuti
    job_queue = application.job_queue
    job_queue.run_repeating(check_updates, interval=300, first=10)
    
    logger.info("🚀 Bot avviato!")
    
    # Avvia il bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
