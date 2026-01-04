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
    
    def add_user_link(self, user_id, link, name):
        user_id = str(user_id)
        if user_id not in self.data['users']:
            self.data['users'][user_id] = {'links': {}}
        link_id = str(len(self.data['users'][user_id]['links']) + 1)
        self.data['users'][user_id]['links'][link_id] = {
            'url': link, 'name': name, 'last_items': [], 'added_at': datetime.now().isoformat()
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
    
    def fetch_vinted_items(self, url):
        try:
            logger.info(f"🔍 Fetching: {url[:100]}")
            response = self.session.get(url, timeout=20)
            logger.info(f"📊 Status: {response.status_code}")
            
            if response.status_code != 200:
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            all_links = soup.find_all('a', href=re.compile(r'/items/\d+'))
            logger.info(f"🔗 Link articoli: {len(all_links)}")
            
            items = []
            seen_ids = set()
            
            for link in all_links[:30]:
                try:
                    href = link.get('href', '')
                    id_match = re.search(r'/items/(\d+)', href)
                    if not id_match or id_match.group(1) in seen_ids:
                        continue
                    
                    item_id = id_match.group(1)
                    seen_ids.add(item_id)
                    url = href if href.startswith('http') else f"https://www.vinted.it{href}"
                    search_area = link.find_parent(['div', 'article']) or link
                    
                    # Titolo
                    title = link.get('title') or link.get_text(strip=True) or "Articolo"
                    if title == "Articolo" and search_area:
                        for div in search_area.find_all(['div', 'p', 'span', 'h2', 'h3']):
                            text = div.get_text(strip=True)
                            clean = text.replace(',','').replace('.','').replace('€','').replace(' ','')
                            if len(text) > 10 and not clean.isdigit():
                                title = text
                                break
                    
                    # Prezzo
                    price = "0"
                    if search_area:
                        all_text = search_area.get_text()
                        pm = re.search(r'(\d+(?:[,.]\d+)?)\s*€', all_text)
                        if pm:
                            price = pm.group(1).replace(',', '.')
                    
                    # Foto
                    photo = None
                    if search_area:
                        img = search_area.find('img')
                        if img:
                            photo = img.get('src') or img.get('data-src')
                    
                    if title != "Articolo" or price != "0":
                        items.append({'id': item_id, 'title': title[:100], 'price': price, 'currency': '€', 'url': url, 'photo': photo})
                except:
                    continue
            
            logger.info(f"✅ Trovati {len(items)} articoli")
            return items[:20]
        except Exception as e:
            logger.error(f"❌ Errore: {e}")
            return []
    
    def check_new_items(self, user_id, link_id):
        user_id = str(user_id)
        if user_id not in self.data['users']:
            return []
        link_data = self.data['users'][user_id]['links'].get(link_id)
        if not link_data:
            return []
        
        current = self.fetch_vinted_items(link_data['url'])
        if not current:
            return []
        
        current_ids = {i['id'] for i in current}
        last_ids = {i['id'] for i in link_data['last_items']}
        new_ids = current_ids - last_ids
        new_items = [i for i in current if i['id'] in new_ids]
        
        link_data['last_items'] = current
        link_data['last_check'] = datetime.now().isoformat()
        self.save_data()
        return new_items

monitor = VintedMonitor()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 <b>Bot Vinted Notifier</b>\n\n"
        "Comandi:\n/aggiungi - Aggiungi link\n/lista - I tuoi link\n"
        "/test - Test\n/rimuovi - Rimuovi",
        parse_mode='HTML'
    )

async def aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔗 Inviami il link Vinted + nome:\n<code>https://... Nome</code>", parse_mode='HTML')

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    if not links:
        await update.message.reply_text("📭 Nessun link")
        return
    msg = "📋 <b>Link:</b>\n\n"
    for lid, d in links.items():
        msg += f"🔹 #{lid} - {d['name']}\n   📦 {len(d.get('last_items', []))} articoli\n\n"
    await update.message.reply_text(msg, parse_mode='HTML')

async def test_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = monitor.get_user_links(update.effective_user.id)
    if not links:
        await update.message.reply_text("📭 Nessun link")
        return
    
    await update.message.reply_text("🔍 Testing...")
    for lid, data in links.items():
        items = monitor.fetch_vinted_items(data['url'])
        if items:
            await update.message.reply_text(f"✅ <b>{data['name']}</b>\n\n{len(items)} articoli!", parse_mode='HTML')
            for i, item in enumerate(items[:3], 1):
                caption = f"<b>{item['title']}</b>\n\n💰 {item['price']} {item['currency']}\n🔗 <a href='{item['url']}'>Vedi</a>"
                try:
                    if item['photo']:
                        await update.message.reply_photo(item['photo'], caption=caption, parse_mode='HTML')
                    else:
                        await update.message.reply_text(caption, parse_mode='HTML')
                except:
                    await update.message.reply_text(caption, parse_mode='HTML')
                await asyncio.sleep(1)
        else:
            await update.message.reply_text(f"⚠️ <b>{data['name']}</b>\n\nNessun articolo", parse_mode='HTML')

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
            await update.message.reply_text(f"✅ Link aggiunto!\n\n🏷️ {name}\n🆔 #{link_id}\n📦 {len(items)} articoli", parse_mode='HTML')
            for i, item in enumerate(items[:3], 1):
                caption = f"<b>{item['title']}</b>\n\n💰 {item['price']} €\n🔗 <a href='{item['url']}'>Vedi</a>"
                try:
                    if item['photo']:
                        await update.message.reply_photo(item['photo'], caption=caption, parse_mode='HTML')
                    else:
                        await update.message.reply_text(caption, parse_mode='HTML')
                except:
                    await update.message.reply_text(caption, parse_mode='HTML')
                await asyncio.sleep(0.5)
        else:
            await update.message.reply_text(f"⚠️ Link aggiunto\n\n🏷️ {name}\n🆔 #{link_id}\n\nNessun articolo trovato", parse_mode='HTML')

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'cancel':
        await query.edit_message_text("❌ Annullato")
    elif query.data.startswith('remove_'):
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
                    msg = f"🆕 <b>{item['title']}</b>\n💰 {item['price']}€\n🔗 <a href='{item['url']}'>Vedi</a>"
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
    logger.info("🚀 BOT VINTED AVVIATO!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
