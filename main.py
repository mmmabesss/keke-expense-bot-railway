#!/usr/bin/env python3
"""
Streamlined Dog Expense Tracker Bot - Railway Version
===================================================
Complete production-ready version optimized for Railway deployment
"""

import os
import logging
import smtplib
import json
from datetime import datetime, timedelta
from typing import Dict, List
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import uuid

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION - Using Environment Variables for Railway
# =============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "Dog Expense Tracker")

# Email configuration
EMAIL_CONFIG = {
    'smtp_server': 'smtp.gmail.com',
    'smtp_port': 587,
    'sender_email': os.getenv("SENDER_EMAIL"),
    'sender_password': os.getenv("SENDER_PASSWORD"),
}

# User configuration
USER_EMAILS = {
    179080995: "mabelkohjw@gmail.com",
    75259354: "jy.koh.jy@gmail.com"
}

AUTHORIZED_USERS = {
    179080995: "Mabel",
    75259354: "Jade"
}

# Validate required environment variables
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN environment variable is required!")
    raise ValueError("BOT_TOKEN environment variable is required")

# Conversation states
(DATE, AMOUNT, PAYER, SPLIT, DESCRIPTION, EDIT_CHOICE, EDIT_VALUE, 
 EDIT_PAYER, EDIT_SPLIT, SETTLEMENT_AMOUNT) = range(10)

# =============================================================================
# DATA HANDLER WITH EMAIL ICS INTEGRATION
# =============================================================================

class ExpenseTracker:
    def __init__(self):
        self.sheet = None
        self._setup_sheets()
    
    def _setup_sheets(self):
        """Setup Google Sheets connection using environment variable or file"""
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            
            # Try to get credentials from environment variable first (Railway)
            creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
            if creds_json:
                logger.info("🔑 Using Google credentials from environment variable")
                creds_info = json.loads(creds_json)
                creds = Credentials.from_service_account_info(creds_info, scopes=scope)
            else:
                # Fallback to file (local development)
                logger.info("🔑 Using Google credentials from file")
                if not os.path.exists("credentials.json"):
                    logger.error("❌ No Google credentials found! Set GOOGLE_CREDENTIALS_JSON env var or add credentials.json file")
                    return
                creds = Credentials.from_service_account_file("credentials.json", scopes=scope)
            
            gc = gspread.authorize(creds)
            
            try:
                self.sheet = gc.open(SPREADSHEET_NAME).sheet1
                headers = ["Date", "Category", "Amount", "Paid By", "Description", "Entry Date", "User ID", "ID", "Mabel Share", "Sister Share"]
                if not self.sheet.row_values(1) or self.sheet.row_values(1) != headers:
                    self.sheet.clear()
                    self.sheet.append_row(headers)
            except gspread.SpreadsheetNotFound:
                logger.info(f"📊 Creating new spreadsheet: {SPREADSHEET_NAME}")
                spreadsheet = gc.create(SPREADSHEET_NAME)
                self.sheet = spreadsheet.sheet1
                self.sheet.append_row(["Date", "Category", "Amount", "Paid By", "Description", "Entry Date", "User ID", "ID", "Mabel Share", "Sister Share"])
            
            logger.info("✅ Connected to Google Sheets successfully")
        except Exception as e:
            logger.error(f"❌ Sheets setup failed: {e}")
            self.sheet = None
    
    def create_ics_file(self, event_type: str, current_date: str, next_due_date: str, description: str = "") -> str:
        """Create an ICS calendar file content"""
        try:
            event_uid = str(uuid.uuid4())
            due_datetime = datetime.strptime(next_due_date, '%Y-%m-%d')
            current_time = datetime.now()
            
            if event_type == 'vaccination':
                summary = "🐩 Keke Vaccination Appointment"
                event_description = f"Annual vaccination appointment for Keke.\\n\\nLast vaccination: {current_date}\\nDue date: {next_due_date}\\n\\nNotes: {description}"
            else:  # blood_test
                summary = "🐩 Keke Blood Test Appointment"
                event_description = f"Semi-annual blood test appointment for Keke.\\n\\nLast blood test: {current_date}\\nDue date: {next_due_date}\\n\\nNotes: {description}"
            
            due_date_ics = due_datetime.strftime('%Y%m%d')
            created_time = current_time.strftime('%Y%m%dT%H%M%SZ')
            
            ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Dog Expense Tracker//Calendar Event//EN
BEGIN:VEVENT
UID:{event_uid}
DTSTAMP:{created_time}
DTSTART;VALUE=DATE:{due_date_ics}
DTEND;VALUE=DATE:{due_date_ics}
SUMMARY:{summary}
DESCRIPTION:{event_description}
BEGIN:VALARM
TRIGGER:-P14D
ACTION:EMAIL
SUMMARY:Reminder: {summary}
DESCRIPTION:Your dog's {event_type.replace('_', ' ')} is due in 2 weeks on {next_due_date}
END:VALARM
BEGIN:VALARM
TRIGGER:-P7D
ACTION:DISPLAY
SUMMARY:Reminder: {summary}
DESCRIPTION:Your dog's {event_type.replace('_', ' ')} is due in 1 week on {next_due_date}
END:VALARM
END:VEVENT
END:VCALENDAR"""
            
            return ics_content
            
        except Exception as e:
            logger.error(f"Error creating ICS file: {e}")
            return None
    
    def send_calendar_email(self, event_type: str, current_date: str, next_due_date: str, description: str = "") -> bool:
        """Send ICS calendar file via email to both users"""
        try:
            sender_email = EMAIL_CONFIG.get('sender_email')
            sender_password = EMAIL_CONFIG.get('sender_password')
            
            logger.info(f"📧 Email config check - Sender: {sender_email}")
            
            if not sender_email:
                logger.warning("📧 Email not configured - sender email missing")
                return False
                
            if not sender_password:
                logger.warning("📧 Email not configured - sender password missing")
                return False
            
            logger.info(f"✅ Email config looks good - attempting to send calendar invite")
            
            ics_content = self.create_ics_file(event_type, current_date, next_due_date, description)
            if not ics_content:
                logger.error("❌ Failed to create ICS content")
                return False
            
            logger.info("✅ ICS content created successfully")
            
            if event_type == 'vaccination':
                subject = f"🐩 Keke due for next Vaccination - {next_due_date}"
                body = f"""Hi!

A new vaccination appointment has been scheduled for Keke.

📅 Appointment Date: {next_due_date}
📝 Last Vaccination: {current_date}
📋 Notes: {description}

The attached calendar file (.ics) can be opened with any calendar app:
• iPhone/Mac: Tap to add to Apple Calendar
• Android: Open with Google Calendar
• Outlook: Import into Outlook Calendar
• Others: Import into your preferred calendar app

The appointment includes automatic reminders 2 weeks and 1 week before the due date.

Best regards,
Dog Expense Tracker Bot 🐩"""
            else:  # blood_test
                subject = f"🐩 Keke due for next Blood Test - {next_due_date}"
                body = f"""Hi!

A new blood test appointment has been scheduled for Keke.

📅 Appointment Date: {next_due_date}
📝 Last Blood Test: {current_date}
📋 Notes: {description}

The attached calendar file (.ics) can be opened with any calendar app:
• iPhone/Mac: Tap to add to Apple Calendar
• Android: Open with Google Calendar
• Outlook: Import into Outlook Calendar
• Others: Import into your preferred calendar app

The appointment includes automatic reminders 2 weeks and 1 week before the due date.

Best regards,
Dog Expense Tracker Bot 🐩"""
            
            for user_email in USER_EMAILS.values():
                logger.info(f"📨 Preparing email for: {user_email}")
                
                try:
                    msg = MIMEMultipart()
                    msg['From'] = sender_email
                    msg['To'] = user_email
                    msg['Subject'] = subject
                    
                    msg.attach(MIMEText(body, 'plain'))
                    
                    attachment = MIMEBase('text', 'calendar')
                    attachment.set_payload(ics_content.encode('utf-8'))
                    encoders.encode_base64(attachment)
                    attachment.add_header(
                        'Content-Disposition',
                        f'attachment; filename="dog_{event_type}_{next_due_date}.ics"'
                    )
                    msg.attach(attachment)
                    
                    logger.info(f"📤 Connecting to SMTP server...")
                    
                    server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
                    server.starttls()
                    
                    logger.info(f"🔐 Logging in with email: {sender_email}")
                    server.login(sender_email, sender_password)
                    
                    logger.info(f"📧 Sending email to: {user_email}")
                    server.send_message(msg)
                    server.quit()
                    
                    logger.info(f"✅ Calendar invite sent successfully to {user_email}")
                    
                except smtplib.SMTPAuthenticationError as e:
                    logger.error(f"❌ SMTP Authentication failed: {e}")
                    logger.error("🔍 Check: 1) 2FA enabled on bot account, 2) App password is correct, 3) Using bot account credentials")
                    return False
                except smtplib.SMTPException as e:
                    logger.error(f"❌ SMTP error sending to {user_email}: {e}")
                    return False
                except Exception as e:
                    logger.error(f"❌ Unexpected error sending to {user_email}: {e}")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"❌ General error in send_calendar_email: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False
    
    def get_next_due_dates(self) -> Dict:
        """Calculate next vaccination and blood test due dates"""
        try:
            if not self.sheet:
                return {}
            
            records = self.sheet.get_all_records()
            latest_vaccination = None
            latest_blood_test = None
            
            for record in records:
                if record.get('Category') == 'Vaccination':
                    date_str = record.get('Date', '')
                    try:
                        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                        if not latest_vaccination or date_obj > latest_vaccination:
                            latest_vaccination = date_obj
                    except ValueError:
                        continue
                
                elif record.get('Category') == 'Blood Test':
                    date_str = record.get('Date', '')
                    try:
                        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                        if not latest_blood_test or date_obj > latest_blood_test:
                            latest_blood_test = date_obj
                    except ValueError:
                        continue
            
            next_dates = {}
            
            if latest_vaccination:
                next_vaccination = latest_vaccination + timedelta(days=365)
                next_dates['vaccination'] = {
                    'last_date': latest_vaccination.strftime('%Y-%m-%d'),
                    'next_date': next_vaccination.strftime('%Y-%m-%d'),
                    'days_until': (next_vaccination - datetime.now()).days
                }
            
            if latest_blood_test:
                next_blood_test = latest_blood_test + timedelta(days=183)
                next_dates['blood_test'] = {
                    'last_date': latest_blood_test.strftime('%Y-%m-%d'),
                    'next_date': next_blood_test.strftime('%Y-%m-%d'),
                    'days_until': (next_blood_test - datetime.now()).days
                }
            
            return next_dates
            
        except Exception as e:
            logger.error(f"Error calculating next due dates: {e}")
            return {}
    
    def add_expense(self, date: str, category: str, amount: float, paid_by: str, description: str, user_id: int, mabel_share: float = None, sister_share: float = None) -> tuple:
        """Add expense to sheet and handle vaccination/blood test scheduling"""
        try:
            if not self.sheet:
                return False, None
            
            entry_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{user_id}"
            if mabel_share is None:
                mabel_share = sister_share = amount / 2
            
            row = [date, category, amount, paid_by, description, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id, entry_id, mabel_share, sister_share]
            self.sheet.append_row(row)
            
            next_appointment_date = None
            if category in ["Vaccination", "Blood Test"]:
                next_appointment_date = self._schedule_next_reminder(category, date, description)
            
            return True, next_appointment_date
        except Exception as e:
            logger.error(f"Error adding expense: {e}")
            return False, None
    
    def _schedule_next_reminder(self, category: str, current_date: str, description: str):
        """Create calendar appointment for the next due date immediately"""
        try:
            current_datetime = datetime.strptime(current_date, '%Y-%m-%d')
            
            if category == "Vaccination":
                next_due = current_datetime + timedelta(days=365)
                event_description = f"Annual vaccination appointment. Last vaccination: {current_date}. Notes: {description}"
            elif category == "Blood Test":
                next_due = current_datetime + timedelta(days=183)
                event_description = f"Semi-annual blood test appointment. Last blood test: {current_date}. Notes: {description}"
            
            success = self.send_calendar_email(
                event_type=category.lower().replace(' ', '_'),
                current_date=current_date,
                next_due_date=next_due.strftime('%Y-%m-%d'),
                description=event_description
            )
            
            if success:
                logger.info(f"✅ Calendar appointment created for {category} on {next_due.strftime('%Y-%m-%d')}")
                return next_due.strftime('%Y-%m-%d')
            else:
                logger.warning(f"⚠️ Failed to create {category} calendar appointment")
                return None
                
        except Exception as e:
            logger.error(f"Error creating calendar appointment: {e}")
            return None
    
    def get_reminders_status(self) -> str:
        """Get current reminder status for display"""
        try:
            next_dates = self.get_next_due_dates()
            
            if not next_dates:
                return "ℹ️ No vaccination or blood test records found yet."
            
            status_lines = []
            
            if 'vaccination' in next_dates:
                vax = next_dates['vaccination']
                days = vax['days_until']
                if days > 0:
                    status_lines.append(f"💉 Next vaccination: {vax['next_date']} ({days} days)")
                else:
                    status_lines.append(f"💉 Vaccination overdue! Due: {vax['next_date']} ({abs(days)} days ago)")
            
            if 'blood_test' in next_dates:
                blood = next_dates['blood_test']
                days = blood['days_until']
                if days > 0:
                    status_lines.append(f"🩸 Next blood test: {blood['next_date']} ({days} days)")
                else:
                    status_lines.append(f"🩸 Blood test overdue! Due: {blood['next_date']} ({abs(days)} days ago)")
            
            return "\n".join(status_lines)
            
        except Exception as e:
            logger.error(f"Error getting reminder status: {e}")
            return "❌ Error retrieving reminder status"
    
    def add_settlement(self, from_user: str, to_user: str, amount: float, user_id: int) -> bool:
        """Add settlement payment"""
        try:
            if not self.sheet:
                return False
            
            entry_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{user_id}_settlement"
            mabel_share = -amount if from_user == "Mabel" else amount
            sister_share = amount if from_user == "Mabel" else -amount
            
            row = [datetime.now().strftime('%Y-%m-%d'), "Settlement Payment", 0, f"{from_user} → {to_user}", 
                   f"Settlement: {from_user} paid {to_user}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                   user_id, entry_id, mabel_share, sister_share]
            self.sheet.append_row(row)
            return True
        except Exception as e:
            logger.error(f"Error adding settlement: {e}")
            return False
    
    def get_recent_entries(self, limit: int = 5) -> List[Dict]:
        """Get recent entries sorted by expense date (not entry date)"""
        try:
            if not self.sheet:
                return []
            records = self.sheet.get_all_records()
            
            # Sort by the actual expense date (Date field), not entry date
            def get_date_for_sorting(record):
                try:
                    date_str = record.get('Date', '')
                    if date_str:
                        return datetime.strptime(date_str, '%Y-%m-%d')
                    else:
                        # If no date, use a very old date so it goes to the bottom
                        return datetime(1900, 1, 1)
                except ValueError:
                    # If date parsing fails, use a very old date
                    return datetime(1900, 1, 1)
            
            sorted_records = sorted(records, key=get_date_for_sorting, reverse=True)
            return sorted_records[:limit]
        except Exception as e:
            logger.error(f"Error getting entries: {e}")
            return []
    
    def get_summary(self) -> Dict:
        """Calculate spending summary"""
        try:
            if not self.sheet:
                return {}
            
            records = self.sheet.get_all_records()
            total_spent = 0
            user_payments = {"Mabel": 0, "Jade": 0}
            user_shares = {"Mabel": 0, "Jade": 0}
            
            for record in records:
                try:
                    amount = float(record.get('Amount', 0))
                    paid_by = record.get('Paid By', '')
                    mabel_share = float(record.get('Mabel Share', amount / 2))
                    sister_share = float(record.get('Sister Share', amount / 2))
                    
                    if record.get('Category', '') != 'Settlement Payment':
                        total_spent += amount
                        if paid_by in user_payments:
                            user_payments[paid_by] += amount
                    
                    user_shares["Mabel"] += mabel_share
                    user_shares["Jade"] += sister_share
                except (ValueError, TypeError):
                    continue
            
            balances = {user: user_payments[user] - user_shares[user] for user in ["Mabel", "Jade"]}
            
            return {
                'total_spent': total_spent,
                'user_payments': user_payments,
                'user_shares': user_shares,
                'balances': balances
            }
        except Exception as e:
            logger.error(f"Error calculating summary: {e}")
            return {}
    
    def update_entry(self, entry_id: str, updates: dict) -> bool:
        """Update entry fields with enhanced debugging and error handling"""
        try:
            if not self.sheet:
                logger.error("🔧 No sheet connection")
                return False
            
            # Get all records including headers
            all_values = self.sheet.get_all_values()
            if not all_values:
                logger.error("🔧 No data found in sheet")
                return False
            
            headers = all_values[0]
            logger.info(f"🔧 Sheet headers: {headers}")
            
            # Create field map based on actual headers (0-indexed for list, but 1-indexed for update_cell)
            field_map = {}
            for i, header in enumerate(headers):
                field_map[header] = i + 1  # +1 because update_cell uses 1-indexed columns
            
            logger.info(f"🔧 Field map: {field_map}")
            logger.info(f"🔧 Updating entry {entry_id} with: {updates}")
            
            # Find the row with matching ID
            target_row = None
            for i, row in enumerate(all_values[1:], start=2):  # Start from row 2 (skip header)
                if len(row) > 7:  # Make sure row has enough columns
                    current_id = row[7] if len(row) > 7 else ''  # ID is in column 8 (index 7)
                    if current_id == entry_id:
                        target_row = i
                        logger.info(f"🔧 ✅ Found entry at row {i}")
                        break
            
            if not target_row:
                logger.error(f"🔧 ❌ Entry {entry_id} not found")
                # Debug: show available IDs
                available_ids = []
                for row in all_values[1:]:
                    if len(row) > 7:
                        available_ids.append(row[7])
                logger.info(f"🔧 Available IDs: {available_ids[:10]}")
                return False
            
            # Update each field
            for field, value in updates.items():
                if field in field_map:
                    col_index = field_map[field]
                    logger.info(f"🔧 Updating {field} in column {col_index} to: '{value}'")
                    
                    try:
                        # Update the cell - using 1-indexed row and column
                        self.sheet.update_cell(target_row, col_index, str(value))
                        logger.info(f"🔧 ✅ Successfully updated {field}")
                    except Exception as cell_error:
                        logger.error(f"🔧 ❌ Error updating {field}: {cell_error}")
                        return False
                else:
                    logger.warning(f"🔧 ⚠️ Field '{field}' not found in headers")
                    logger.info(f"🔧 Available fields: {list(field_map.keys())}")
            
            logger.info("🔧 ✅ Entry update completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"🔧 ❌ Error in update_entry: {e}")
            import traceback
            logger.error(f"🔧 Full traceback: {traceback.format_exc()}")
            return False
    
    def delete_entry(self, entry_id: str) -> bool:
        """Delete entry"""
        try:
            if not self.sheet:
                return False
            
            records = self.sheet.get_all_values()
            for i, row in enumerate(records):
                if i > 0 and len(row) > 7 and row[7] == entry_id:
                    self.sheet.delete_rows(i + 1)
                    return True
            return False
        except Exception as e:
            logger.error(f"Error deleting entry: {e}")
            return False
    
    def get_entry_by_id(self, entry_id: str) -> Dict:
        """Get specific entry"""
        try:
            records = self.sheet.get_all_records() if self.sheet else []
            return next((r for r in records if r.get('ID') == entry_id), {})
        except Exception:
            return {}

# Global tracker instance
tracker = ExpenseTracker()

# =============================================================================
# UTILITIES
# =============================================================================

def check_auth(func):
    """Authorization decorator"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in AUTHORIZED_USERS:
            await update.message.reply_text(f"❌ Not authorized. Your ID: {user_id}")
            return
        return await func(update, context)
    return wrapper

def create_menu_keyboard():
    """Create main menu keyboard"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏥 Log Vet Visit", callback_data="log_vet")],
        [InlineKeyboardButton("💉 Log Vaccination", callback_data="log_vaccination")],
        [InlineKeyboardButton("🩸 Log Blood Test", callback_data="log_blood_test")],
        [InlineKeyboardButton("🔬 Log Other Vet Item", callback_data="log_other_vet")],
        [InlineKeyboardButton("🛒 Log Other Expense", callback_data="log_other_expense")],
        [InlineKeyboardButton("📋 View Recent Logs", callback_data="view_recent")],
        [InlineKeyboardButton("✏️ Edit Past Log", callback_data="edit_log")],
        [InlineKeyboardButton("💰 View Summary", callback_data="view_summary")],
        [InlineKeyboardButton("📅 View Reminders", callback_data="view_reminders")],
    ])

# =============================================================================
# COMMAND HANDLERS
# =============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text(f"❌ Not authorized. Your ID: {user_id}")
        return
    
    user_name = AUTHORIZED_USERS[user_id]
    await update.message.reply_text(f"🐕 Welcome {user_name}! Use /menu to begin.")

@check_auth
async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show main menu"""
    await update.message.reply_text(
        "🐕 **Dog Expense Tracker**\nChoose an option:",
        reply_markup=create_menu_keyboard(),
        parse_mode='Markdown'
    )

# =============================================================================
# BUTTON HANDLERS
# =============================================================================

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle main menu button presses"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in AUTHORIZED_USERS:
        await query.edit_message_text("❌ Not authorized.")
        return
    
    data = query.data
    
    if data == "view_recent":
        await show_recent_entries(query)
    elif data == "view_summary":
        await show_summary(query)
    elif data == "view_reminders":
        await show_reminders(query)
    elif data == "edit_log":
        await show_edit_menu(query)
    elif data.startswith("settle_"):
        await handle_settlement_start(update, context)

async def show_reminders(query):
    """Show vaccination and blood test reminders"""
    reminder_status = tracker.get_reminders_status()
    next_dates = tracker.get_next_due_dates()
    
    message = f"📅 **Health Reminders**\n\n{reminder_status}\n\n"
    
    if next_dates:
        message += "ℹ️ **How it works:**\n"
        message += "• Calendar invites sent 2 weeks before due dates\n"
        message += "• Vaccinations: Annual (every 12 months)\n"
        message += "• Blood tests: Semi-annual (every 6 months)\n"
        message += "• Both you and your sister receive invites"
    else:
        message += "💡 **Get started:**\n"
        message += "Log your first vaccination or blood test entry to start automatic reminders!"
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    await query.edit_message_text(message, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def show_recent_entries(query):
    """Show recent entries"""
    entries = tracker.get_recent_entries(10)
    
    # Filter out settlement payments
    filtered_entries = [entry for entry in entries if entry.get('Category', '') != 'Settlement Payment']
    
    if not filtered_entries:
        message = "📋 No entries found."
    else:
        message = "📋 **Recent Entries** (Last 10)\n\n"
        for i, entry in enumerate(filtered_entries, 1):
            amount = entry.get('Amount', 0)
            category = entry.get('Category', 'Unknown')
            
            if amount == 0:
                amount_text = "Tracking only"
            else:
                amount_text = f"${amount:.2f} - {entry.get('Paid By', 'Unknown')}"
            
            message += f"{i}. **{category}**\n   📅 {entry.get('Date', '')}\n   💰 {amount_text}\n   📝 {entry.get('Description', '')}\n\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    await query.edit_message_text(message, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def show_summary(query):
    """Show spending summary"""
    summary = tracker.get_summary()
    
    if not summary:
        message = "💰 No expenses recorded yet."
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    else:
        message = f"""💰 **Dog Expense Summary**

🐕 **Total Expenses:** ${summary['total_spent']:.2f}

💳 **Payments Made:**
• Mabel paid: ${summary['user_payments']['Mabel']:.2f}
• Jade paid: ${summary['user_payments']['Jade']:.2f}

📊 **Fair Shares:**
• Mabel's share: ${summary['user_shares']['Mabel']:.2f}
• Jade's share: ${summary['user_shares']['Jade']:.2f}

💸 **Settlement:**"""
        
        mabel_balance = summary['balances']['Mabel']
        keyboard = []
        
        if abs(mabel_balance) < 0.01:
            message += "\n✅ Everyone is settled up!"
        elif mabel_balance > 0:
            message += f"\n💰 Mabel is owed: ${mabel_balance:.2f}\n🔄 Jade should pay Mabel ${mabel_balance:.2f}"
            keyboard.append([InlineKeyboardButton("💳 Record: Jade paid Mabel", callback_data="settle_jade_mabel")])
        else:
            jade_owed = abs(mabel_balance)
            message += f"\n💰 Jade is owed: ${jade_owed:.2f}\n🔄 Mabel should pay Jade ${jade_owed:.2f}"
            keyboard.append([InlineKeyboardButton("💳 Record: Mabel paid Jade", callback_data="settle_mabel_jade")])
        
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
    
    await query.edit_message_text(message, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def show_edit_menu(query):
    """Show entries available for editing"""
    entries = tracker.get_recent_entries(10)
    
    if not entries:
        message = "📋 No entries to edit."
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    else:
        message = "✏️ **Select Entry to Edit**\n\n"
        keyboard = []
        
        for entry in entries:
            entry_id = entry.get('ID', '')
            category = entry.get('Category', 'Unknown')
            amount = entry.get('Amount', 0)
            date = entry.get('Date', '')
            
            # Debug logging
            logger.info(f"Edit menu entry: ID='{entry_id}', Category='{category}', Amount='{amount}', Date='{date}'")
            
            if not entry_id:
                logger.warning(f"Entry missing ID: {entry}")
                continue
            
            # Create descriptive button text
            if amount == 0 and category != 'Settlement Payment':
                button_text = f"{date} - {category} (Tracking)"
            elif category == 'Settlement Payment':
                button_text = f"{date} - Settlement - {entry.get('Paid By', '')}"
            else:
                button_text = f"{date} - {category} - ${amount:.2f}"
            
            # Make sure button text isn't too long
            if len(button_text) > 60:
                button_text = button_text[:57] + "..."
            
            logger.info(f"Creating button: '{button_text}' with callback_data='edit_{entry_id}'")
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"edit_{entry_id}")])
        
        if not keyboard:
            message = "📋 No valid entries to edit."
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        else:
            keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
    
    await query.edit_message_text(message, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle back to menu"""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    
    await query.edit_message_text(
        "🐕 **Dog Expense Tracker**\nChoose an option:",
        reply_markup=create_menu_keyboard(),
        parse_mode='Markdown'
    )

# =============================================================================
# EXPENSE LOGGING CONVERSATION
# =============================================================================

async def start_expense_logging(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start logging expense"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in AUTHORIZED_USERS:
        await query.edit_message_text("❌ Not authorized.")
        return ConversationHandler.END
    
    type_map = {
        "log_vet": ("Vet Visit", False),
        "log_vaccination": ("Vaccination", True),
        "log_blood_test": ("Blood Test", True),
        "log_other_vet": ("Other Vet", False),
        "log_other_expense": ("Other Expense", False)
    }
    
    category, is_tracking = type_map.get(query.data, ("Unknown", False))
    context.user_data.update({'category': category, 'is_tracking': is_tracking})
    
    if is_tracking:
        context.user_data['amount'] = 0.0
    
    await query.edit_message_text(
        f"📅 **Logging {category}**\n\nEnter date (YYYY-MM-DD) or 'today':",
        parse_mode='Markdown'
    )
    
    return DATE

async def handle_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle date input"""
    text = update.message.text.strip().lower()
    
    if text == 'today':
        date = datetime.now().strftime('%Y-%m-%d')
    else:
        try:
            datetime.strptime(text, '%Y-%m-%d')
            date = text
        except ValueError:
            await update.message.reply_text("❌ Invalid date. Use YYYY-MM-DD or 'today'")
            return DATE
    
    context.user_data['date'] = date
    
    if context.user_data.get('is_tracking'):
        await update.message.reply_text(
            f"📝 **Description**\n\nDate: {date}\nCategory: {context.user_data['category']}\n\nEnter description:"
        )
        return DESCRIPTION
    else:
        await update.message.reply_text(
            f"💰 **Amount**\n\nDate: {date}\nCategory: {context.user_data['category']}\n\nEnter amount:"
        )
        return AMOUNT

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle amount input"""
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Enter a positive number.")
        return AMOUNT
    
    context.user_data['amount'] = amount
    
    keyboard = [[InlineKeyboardButton(name, callback_data=f"payer_{name}")] for name in AUTHORIZED_USERS.values()]
    
    await update.message.reply_text(
        f"👤 **Who Paid?**\n\nAmount: ${amount:.2f}\nWho paid for this expense?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    
    return PAYER

async def handle_payer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payer selection"""
    query = update.callback_query
    await query.answer()
    
    payer = query.data.replace("payer_", "")
    context.user_data['payer'] = payer
    
    keyboard = [
        [InlineKeyboardButton("🔄 Split 50/50", callback_data="split_equal")],
        [InlineKeyboardButton("💰 Custom Split", callback_data="split_custom")]
    ]
    
    await query.edit_message_text(
        f"💸 **Split Method**\n\nAmount: ${context.user_data['amount']:.2f}\nPaid by: {payer}\n\nHow to split?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    
    return SPLIT

async def handle_split(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle split selection"""
    query = update.callback_query
    await query.answer()
    
    amount = context.user_data['amount']
    
    if query.data == "split_equal":
        context.user_data.update({'mabel_share': amount/2, 'sister_share': amount/2})
        
        await query.edit_message_text(
            f"📝 **Description**\n\nAmount: ${amount:.2f}\nPaid by: {context.user_data['payer']}\nSplit: 50/50\n\nEnter description:",
            parse_mode='Markdown'
        )
        return DESCRIPTION
        
    elif query.data == "split_custom":
        payer = context.user_data['payer']
        other = "Jade" if payer == "Mabel" else "Mabel"
        context.user_data['other_person'] = other
        
        await query.edit_message_text(
            f"💰 **Custom Split**\n\nTotal: ${amount:.2f}\nPaid by: {payer}\n\nHow much should {other} pay?",
            parse_mode='Markdown'
        )
        return SPLIT

async def handle_custom_split(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle custom split amount"""
    try:
        other_amount = float(update.message.text.strip())
        total = context.user_data['amount']
        
        if other_amount < 0 or other_amount > total:
            raise ValueError()
        
        payer = context.user_data['payer']
        payer_amount = total - other_amount
        
        if payer == "Mabel":
            context.user_data.update({'mabel_share': payer_amount, 'sister_share': other_amount})
        else:
            context.user_data.update({'mabel_share': other_amount, 'sister_share': payer_amount})
        
    except ValueError:
        await update.message.reply_text(f"❌ Invalid amount. Enter 0 to ${context.user_data['amount']:.2f}")
        return SPLIT
    
    await update.message.reply_text(
        f"📝 **Description**\n\nAmount: ${total:.2f}\nSplit: Custom\n\nEnter description:"
    )
    return DESCRIPTION

async def handle_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle description and save expense"""
    description = update.message.text.strip()
    
    if not description:
        await update.message.reply_text("❌ Description required.")
        return DESCRIPTION
    
    success, next_appointment_date = tracker.add_expense(
        date=context.user_data['date'],
        category=context.user_data['category'],
        amount=context.user_data['amount'],
        paid_by=context.user_data.get('payer', 'N/A'),
        description=description,
        user_id=update.effective_user.id,
        mabel_share=context.user_data.get('mabel_share'),
        sister_share=context.user_data.get('sister_share')
    )
    
    if success:
        if context.user_data.get('is_tracking'):
            amount_text = "Tracking only"
            category = context.user_data['category']
            if category in ["Vaccination", "Blood Test"] and next_appointment_date:
                if category == "Vaccination":
                    reminder_text = f"\n\n📅 **Next vaccination appointment:** {next_appointment_date}\n📧 **Calendar invite emailed!** Check your email for the .ics file to add to your calendar."
                else:
                    reminder_text = f"\n\n📅 **Next blood test appointment:** {next_appointment_date}\n📧 **Calendar invite emailed!** Check your email for the .ics file to add to your calendar."
            elif category in ["Vaccination", "Blood Test"]:
                reminder_text = f"\n\n⚠️ Calendar invite could not be sent (check email configuration)"
            else:
                reminder_text = ""
        else:
            amount_text = f"${context.user_data['amount']:.2f}"
            reminder_text = ""
        
        await update.message.reply_text(
            f"✅ **Logged Successfully!**\n\n"
            f"📅 {context.user_data['date']}\n"
            f"🏷️ {context.user_data['category']}\n"
            f"💰 {amount_text}\n"
            f"📝 {description}{reminder_text}\n\n"
            f"Use /menu to continue.",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ Error saving. Check your Google Sheets connection.")
    
    context.user_data.clear()
    return ConversationHandler.END

# =============================================================================
# SETTLEMENT CONVERSATION
# =============================================================================

async def handle_settlement_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start settlement recording"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "settle_jade_mabel":
        from_user, to_user = "Jade", "Mabel"
    else:
        from_user, to_user = "Mabel", "Jade"
    
    context.user_data.update({'settlement_from': from_user, 'settlement_to': to_user})
    
    summary = tracker.get_summary()
    suggested = abs(summary['balances'].get(from_user == "Jade" and "Mabel" or "Jade", 0))
    
    await query.edit_message_text(
        f"💳 **Record Settlement**\n\n"
        f"From: {from_user}\nTo: {to_user}\n"
        f"Suggested: ${suggested:.2f}\n\n"
        f"Enter amount {from_user} paid:",
        parse_mode='Markdown'
    )
    
    return SETTLEMENT_AMOUNT

async def handle_settlement_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle settlement amount"""
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
        return SETTLEMENT_AMOUNT
    
    from_user = context.user_data['settlement_from']
    to_user = context.user_data['settlement_to']
    
    success = tracker.add_settlement(from_user, to_user, amount, update.effective_user.id)
    
    if success:
        await update.message.reply_text(
            f"✅ **Settlement Recorded!**\n\n"
            f"💳 {from_user} paid {to_user}: ${amount:.2f}\n\n"
            f"Use /menu to view updated summary.",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ Error recording settlement.")
    
    context.user_data.clear()
    return ConversationHandler.END

# =============================================================================
# EDIT CONVERSATION
# =============================================================================

async def handle_edit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit entry selection"""
    query = update.callback_query
    await query.answer()
    
    # Debug: Log the full callback data
    logger.info(f"🔍 Full callback data received: '{query.data}'")
    
    # Check if this is actually an "edit_log" button press (which should go to show_edit_menu)
    if query.data == "edit_log":
        logger.info("🔍 This is edit_log button, redirecting to show_edit_menu")
        await show_edit_menu(query)
        return ConversationHandler.END
    
    # Extract entry ID from callback data
    if not query.data.startswith("edit_"):
        logger.error(f"🔍 Invalid callback data format: '{query.data}'")
        await query.edit_message_text("❌ Invalid selection. Please try again from the main menu.", parse_mode='Markdown')
        return ConversationHandler.END
    
    entry_id = query.data[5:]  # Remove "edit_" prefix
    logger.info(f"Edit selection: Looking for entry ID '{entry_id}'")
    
    entry = tracker.get_entry_by_id(entry_id)
    logger.info(f"Edit selection: Found entry: {entry}")
    
    if not entry:
        # Try to get all entries and show their IDs for debugging
        all_entries = tracker.get_recent_entries(10)
        logger.error(f"Edit selection: Entry '{entry_id}' not found. Available entries:")
        for e in all_entries:
            logger.error(f"  - ID: '{e.get('ID', 'NO_ID')}', Category: '{e.get('Category', 'NO_CATEGORY')}'")
        
        await query.edit_message_text("❌ Entry not found. Please try again from the main menu.", parse_mode='Markdown')
        return ConversationHandler.END
    
    context.user_data.update({'editing_id': entry_id, 'editing_entry': entry})
    
    category = entry.get('Category', 'Unknown')
    amount = entry.get('Amount', 0)
    
    if category == 'Settlement Payment':
        message = f"✏️ **Edit Settlement**\n\n{entry.get('Description', '')}\n\nYou can only delete settlement entries."
        keyboard = [
            [InlineKeyboardButton("🗑️ Delete", callback_data="delete_confirm")],
            [InlineKeyboardButton("❌ Cancel", callback_data="back_to_menu")]
        ]
    else:
        if amount == 0:
            amount_text = "Tracking only"
        else:
            amount_text = f"${amount:.2f}"
        
        message = f"✏️ **Edit Entry**\n\n📅 {entry.get('Date', '')}\n🏷️ {category}\n💰 {amount_text}\n👤 Paid by: {entry.get('Paid By', 'Unknown')}\n📝 {entry.get('Description', '')}\n\nWhat to edit?"
        
        keyboard = [
            [InlineKeyboardButton("📅 Date", callback_data="edit_date")],
            [InlineKeyboardButton("📝 Description", callback_data="edit_description")]
        ]
        
        if category not in ["Vaccination", "Blood Test"]:
            keyboard.extend([
                [InlineKeyboardButton("💰 Amount", callback_data="edit_amount")],
                [InlineKeyboardButton("👤 Paid By", callback_data="edit_payer")]
            ])
        
        keyboard.extend([
            [InlineKeyboardButton("🗑️ Delete", callback_data="delete_confirm")],
            [InlineKeyboardButton("❌ Cancel", callback_data="back_to_menu")]
        ])
    
    await query.edit_message_text(message, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    return EDIT_CHOICE

async def handle_edit_field_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit field selection"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "delete_confirm":
        success = tracker.delete_entry(context.user_data['editing_id'])
        if success:
            await query.edit_message_text("✅ **Entry Deleted!**\n\nUse /menu to continue.", parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Error deleting entry.", parse_mode='Markdown')
        context.user_data.clear()
        return ConversationHandler.END
    
    elif query.data.startswith("new_payer_"):
        # Handle simple payer change (not amount editing)
        new_payer = query.data.replace("new_payer_", "")
        success = tracker.update_entry(context.user_data['editing_id'], {'Paid By': new_payer})
        
        if success:
            await query.edit_message_text(f"✅ **Updated!**\n\nPaid by changed to: {new_payer}\n\nUse /menu to continue.", parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Error updating entry.", parse_mode='Markdown')
        
        context.user_data.clear()
        return ConversationHandler.END
    
    field = query.data.replace("edit_", "")
    context.user_data['editing_field'] = field
    
    if field == "date":
        await query.edit_message_text("📅 **Edit Date**\n\nEnter new date (YYYY-MM-DD) or 'today':", parse_mode='Markdown')
    elif field == "description":
        await query.edit_message_text("📝 **Edit Description**\n\nEnter new description:", parse_mode='Markdown')
    elif field == "amount":
        await query.edit_message_text("💰 **Edit Amount**\n\nEnter new amount:\n⚠️ This will require updating payment info.", parse_mode='Markdown')
    elif field == "payer":
        keyboard = [[InlineKeyboardButton(name, callback_data=f"new_payer_{name}")] for name in AUTHORIZED_USERS.values()]
        await query.edit_message_text("👤 **Edit Paid By**\n\nWho paid?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return EDIT_CHOICE  # Stay in EDIT_CHOICE state to handle new_payer_ callbacks
    
    return EDIT_VALUE

async def handle_edit_value_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit value input"""
    field = context.user_data['editing_field']
    new_value = update.message.text.strip()
    
    reminder_text = ""
    
    try:
        if field == "date":
            if new_value.lower() == 'today':
                new_value = datetime.now().strftime('%Y-%m-%d')
            else:
                try:
                    datetime.strptime(new_value, '%Y-%m-%d')
                except ValueError:
                    await update.message.reply_text("❌ Invalid date format. Use YYYY-MM-DD or 'today'")
                    return EDIT_VALUE
            
            success = tracker.update_entry(context.user_data['editing_id'], {'Date': new_value})
            
            # Check if this is a vaccination or blood test - if so, send new calendar invite
            entry = context.user_data.get('editing_entry', {})
            category = entry.get('Category', '')
            description = entry.get('Description', '')
            
            if success and category in ["Vaccination", "Blood Test"]:
                try:
                    # Calculate new due date and send calendar invite
                    current_datetime = datetime.strptime(new_value, '%Y-%m-%d')
                    
                    if category == "Vaccination":
                        next_due = current_datetime + timedelta(days=365)
                        event_description = f"Annual vaccination appointment. Last vaccination: {new_value}. Notes: {description}"
                    elif category == "Blood Test":
                        next_due = current_datetime + timedelta(days=183)
                        event_description = f"Semi-annual blood test appointment. Last blood test: {new_value}. Notes: {description}"
                    
                    # Send new calendar email
                    email_success = tracker.send_calendar_email(
                        event_type=category.lower().replace(' ', '_'),
                        current_date=new_value,
                        next_due_date=next_due.strftime('%Y-%m-%d'),
                        description=event_description
                    )
                    
                    if email_success:
                        next_due_formatted = next_due.strftime('%Y-%m-%d')
                        reminder_text = f"\n\n📅 **Updated next {category.lower()} appointment:** {next_due_formatted}\n📧 **New calendar invite emailed!** Check your email for the updated .ics file."
                        logger.info(f"✅ Updated calendar appointment for {category} on {next_due_formatted}")
                    else:
                        reminder_text = f"\n\n⚠️ Date updated but calendar invite could not be sent (check email configuration)"
                        logger.warning(f"⚠️ Failed to send updated {category} calendar appointment")
                        
                except Exception as e:
                    logger.error(f"Error sending updated calendar appointment: {e}")
                    reminder_text = f"\n\n⚠️ Date updated but calendar invite could not be sent"
            
        elif field == "description":
            if not new_value:
                await update.message.reply_text("❌ Description cannot be empty.")
                return EDIT_VALUE
            
            success = tracker.update_entry(context.user_data['editing_id'], {'Description': new_value})
            
        elif field == "amount":
            try:
                amount = float(new_value)
                if amount < 0:
                    raise ValueError()
            except ValueError:
                await update.message.reply_text("❌ Invalid amount. Enter a positive number.")
                return EDIT_VALUE
            
            context.user_data['new_amount'] = amount
            
            keyboard = [[InlineKeyboardButton(name, callback_data=f"edit_amount_payer_{name}")] for name in AUTHORIZED_USERS.values()]
            await update.message.reply_text(
                f"💰 **Amount: ${amount:.2f}**\n\n👤 Who paid this amount?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return EDIT_PAYER
        
        if success:
            await update.message.reply_text(
                f"✅ **Updated!**\n\n{field.title()} changed to: {new_value}{reminder_text}\n\nUse /menu to continue.", 
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Error updating entry. Please try again or contact support.")
        
    except Exception as e:
        logger.error(f"Error in handle_edit_value_input: {e}")
        await update.message.reply_text("❌ An unexpected error occurred. Please try again.")
    
    context.user_data.clear()
    return ConversationHandler.END

async def handle_edit_payer_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payer selection during edit"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("new_payer_"):
        new_payer = query.data.replace("new_payer_", "")
        success = tracker.update_entry(context.user_data['editing_id'], {'Paid By': new_payer})
        
        if success:
            await query.edit_message_text(f"✅ **Updated!**\n\nPaid by changed to: {new_payer}\n\nUse /menu to continue.", parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Error updating entry.", parse_mode='Markdown')
        
        context.user_data.clear()
        return ConversationHandler.END
        
    elif query.data.startswith("edit_amount_payer_"):
        new_payer = query.data.replace("edit_amount_payer_", "")
        context.user_data['new_payer'] = new_payer
        
        amount = context.user_data['new_amount']
        
        if amount == 0:
            updates = {
                'Amount': amount,
                'Paid By': new_payer,
                'Mabel Share': 0,
                'Sister Share': 0
            }
            success = tracker.update_entry(context.user_data['editing_id'], updates)
            
            if success:
                await query.edit_message_text("✅ **Amount Updated!**\n\nUse /menu to continue.", parse_mode='Markdown')
            else:
                await query.edit_message_text("❌ Error updating entry.", parse_mode='Markdown')
            
            context.user_data.clear()
            return ConversationHandler.END
        
        keyboard = [
            [InlineKeyboardButton("🔄 Split 50/50", callback_data="edit_split_equal")],
            [InlineKeyboardButton("💰 Custom Split", callback_data="edit_split_custom")]
        ]
        
        await query.edit_message_text(
            f"💸 **Split ${amount:.2f}**\n\nPaid by: {new_payer}\n\nHow to split?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        
        return EDIT_SPLIT

async def handle_edit_split_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle split selection during edit"""
    query = update.callback_query
    await query.answer()
    
    amount = context.user_data['new_amount']
    payer = context.user_data['new_payer']
    
    if query.data == "edit_split_equal":
        mabel_share = sister_share = amount / 2
        
        updates = {
            'Amount': amount,
            'Paid By': payer,
            'Mabel Share': mabel_share,
            'Sister Share': sister_share
        }
        
        success = tracker.update_entry(context.user_data['editing_id'], updates)
        
        if success:
            await query.edit_message_text(
                f"✅ **Amount Updated!**\n\n💰 ${amount:.2f}\n👤 {payer}\n💸 Split 50/50\n\n💡 Summary will reflect the new amount.\n\nUse /menu to continue.",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Error updating entry.", parse_mode='Markdown')
        
        context.user_data.clear()
        return ConversationHandler.END
        
    elif query.data == "edit_split_custom":
        other_person = "Jade" if payer == "Mabel" else "Mabel"
        context.user_data['edit_other_person'] = other_person
        
        await query.edit_message_text(
            f"💰 **Custom Split**\n\nTotal: ${amount:.2f}\nPaid by: {payer}\n\nHow much should {other_person} pay?",
            parse_mode='Markdown'
        )
        
        return EDIT_SPLIT

async def handle_edit_custom_split_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle custom split input during edit"""
    try:
        other_amount = float(update.message.text.strip())
        total_amount = context.user_data['new_amount']
        
        if other_amount < 0 or other_amount > total_amount:
            raise ValueError()
        
        payer = context.user_data['new_payer']
        other_person = context.user_data['edit_other_person']
        payer_amount = total_amount - other_amount
        
        if payer == "Mabel":
            mabel_share, sister_share = payer_amount, other_amount
        else:
            mabel_share, sister_share = other_amount, payer_amount
        
        updates = {
            'Amount': total_amount,
            'Paid By': payer,
            'Mabel Share': mabel_share,
            'Sister Share': sister_share
        }
        
        success = tracker.update_entry(context.user_data['editing_id'], updates)
        
        if success:
            await update.message.reply_text(
                f"✅ **Amount Updated!**\n\n💰 ${total_amount:.2f}\n👤 {payer}\n💸 {payer}: ${payer_amount:.2f}, {other_person}: ${other_amount:.2f}\n\n💡 Summary will reflect the new amount.\n\nUse /menu to continue.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Error updating entry.")
        
    except ValueError:
        await update.message.reply_text(f"❌ Invalid amount. Enter 0 to ${context.user_data['new_amount']:.2f}")
        return EDIT_SPLIT
    
    context.user_data.clear()
    return ConversationHandler.END

# =============================================================================
# CONVERSATION HANDLERS
# =============================================================================

def create_expense_handler():
    """Create expense logging conversation handler"""
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start_expense_logging, pattern="^log_")],
        states={
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount)],
            PAYER: [CallbackQueryHandler(handle_payer, pattern="^payer_")],
            SPLIT: [
                CallbackQueryHandler(handle_split, pattern="^split_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_split)
            ],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description)],
        },
        fallbacks=[
            CommandHandler('menu', show_menu),
            CallbackQueryHandler(handle_back_to_menu, pattern="^back_to_menu$")
        ],
        per_user=True
    )

def create_settlement_handler():
    """Create settlement conversation handler"""
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_settlement_start, pattern="^settle_")],
        states={
            SETTLEMENT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settlement_amount)],
        },
        fallbacks=[
            CommandHandler('menu', show_menu),
            CallbackQueryHandler(handle_back_to_menu, pattern="^back_to_menu$")
        ],
        per_user=True,
        allow_reentry=True
    )

def create_edit_handler():
    """Create edit conversation handler"""
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_edit_selection, pattern="^edit_20\\d{6}_\\d{6}_\\d+")],
        states={
            EDIT_CHOICE: [
                CallbackQueryHandler(handle_edit_field_choice, pattern="^(edit_|delete_|new_payer_)")
            ],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_value_input)],
            EDIT_PAYER: [
                CallbackQueryHandler(handle_edit_payer_selection, pattern="^(edit_amount_payer_|new_payer_)")
            ],
            EDIT_SPLIT: [
                CallbackQueryHandler(handle_edit_split_selection, pattern="^edit_split_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_custom_split_input)
            ],
        },
        fallbacks=[
            CommandHandler('menu', show_menu),
            CallbackQueryHandler(handle_back_to_menu, pattern="^back_to_menu$")
        ],
        per_user=True,
        allow_reentry=True
    )

# =============================================================================
# HELP COMMAND
# =============================================================================

@check_auth
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    help_text = """🐕 **Dog Expense Tracker Help**

**Categories:**
• 🏥 Vet Visit - Regular checkups (with cost)
• 💉 Vaccination - Tracking only (no cost)
• 🩸 Blood Test - Tracking only (no cost)  
• 🔬 Other Vet - X-rays, tests, etc. (with cost)
• 🛒 Other Expense - Food, toys, grooming (with cost)

**Features:**
• 💰 Custom expense splitting
• 💳 Settlement payment tracking
• ✏️ Edit/delete entries
• 📊 Spending summaries
• 📅 Automatic health reminders

**Health Reminders:**
• 💉 Vaccinations: Annual reminders (12 months)
• 🩸 Blood Tests: Semi-annual reminders (6 months)
• 📧 Calendar invites sent to both users
• 🔔 Reminders start 2 weeks before due date

**Commands:**
• /menu - Main menu
• /help - This help

**Tips:**
• Use 'today' for current date
• Settlement payments automatically update balances
• Editing amounts requires updating payment info
• Calendar invites sent automatically when logging vaccinations/blood tests"""

    await update.message.reply_text(help_text, parse_mode='Markdown')

# =============================================================================
# ERROR HANDLER
# =============================================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Error: {context.error}")
    
    try:
        if update and hasattr(update, 'effective_user'):
            user_id = update.effective_user.id
            if user_id in AUTHORIZED_USERS:
                if hasattr(update, 'message') and update.message:
                    await update.message.reply_text("❌ An error occurred. Use /menu to restart.")
                elif hasattr(update, 'callback_query') and update.callback_query:
                    await update.callback_query.message.reply_text("❌ An error occurred. Use /menu to restart.")
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

# =============================================================================
# MAIN FUNCTION - Railway Optimized
# =============================================================================

def main():
    """Run the bot optimized for Railway"""
    
    logger.info("🚂 Starting Dog Expense Tracker Bot on Railway...")
    
    # Validate environment
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not found in environment variables!")
        logger.error("💡 Add your Telegram bot token to Railway environment variables as 'BOT_TOKEN'")
        return
    
    # Initialize tracker
    global tracker
    if not tracker.sheet:
        logger.error("❌ Failed to connect to Google Sheets")
        logger.error("💡 Check your GOOGLE_CREDENTIALS_JSON environment variable")
        return
    
    logger.info("✅ Google Sheets connection successful")
    
    # Create application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", show_menu))
    app.add_handler(CommandHandler("help", help_command))
    
    # Conversation handlers - order matters!
    app.add_handler(create_settlement_handler())
    app.add_handler(create_expense_handler())
    app.add_handler(create_edit_handler())
    
    # Button handlers - Fixed order and patterns to avoid conflicts
    app.add_handler(CallbackQueryHandler(handle_menu_buttons, pattern="^(view_recent|view_summary|view_reminders|edit_log)$"))
    app.add_handler(CallbackQueryHandler(handle_menu_buttons, pattern="^settle_(jade_mabel|mabel_jade)$"))
    app.add_handler(CallbackQueryHandler(handle_back_to_menu, pattern="^back_to_menu$"))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    logger.info("✅ Bot handlers configured successfully")
    logger.info("🚀 Bot is now running on Railway!")
    logger.info("🔗 Your bot will run 24/7 on Railway's infrastructure")
    
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        raise

if __name__ == '__main__':
    main()