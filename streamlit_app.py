import streamlit as st
import pandas as pd
import calendar
from datetime import datetime, date # IMPORT ESPLICITO
import holidays
from io import BytesIO
import requests
import json
import base64
import time
import logging
import tempfile
import os
from functools import wraps # Per wraps in retry_github_api
import unicodedata # Per valida_nome_medico_v2 (Suggerimento 5)
import re
import hashlib
from openpyxl.styles import PatternFill, Font, Alignment

# --- CONFIGURAZIONE LOGGING ---
def setup_logging():
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]: root_logger.removeHandler(handler); handler.close()
    log_file_path = 'medical_shifts.log'
    log_handlers = [logging.StreamHandler()]
    try:
        with open(log_file_path, 'a'): pass
        log_handlers.append(logging.FileHandler(log_file_path, mode='a', encoding='utf-8'))
    except Exception as e: print(f"Attenzione: Errore logging su file ({log_file_path}): {e}. Logging su file disabilitato.")
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=log_handlers)
    return logging.getLogger(__name__)

logger = setup_logging()
logger.info("--- Applicazione Gestione Turni Medici AVVIATA ---")

# --- CONFIGURAZIONE INIZIALE STREAMLIT ---
st.set_page_config(page_title="Gestione Turni Medici", layout="wide", initial_sidebar_state="expanded")

# --- COSTANTI (Suggerimento 2) ---
COL_DATA = "Data"
COL_GIORNO = "Giorno"
COL_FESTIVO = "Festivo" 
COL_NOME_FESTIVO = "Nome Festivo"
TIPI_ASSENZA = ["Presente", "Ferie", "Malattia", "Congresso", "Lezione", "Altro"]
MEDICI_BACKUP_FILE = "medici_backup.json"

ROW_HEIGHT_PX = 35
TABLE_PADDING_PX = 3
MIN_COLUMN_WIDTH_EXCEL = 12
MAX_COLUMN_WIDTH_EXCEL = 45
COLUMN_PADDING_EXCEL = 3

# --- CONFIGURAZIONE APPLICAZIONE (Suggerimento 6) ---
class AppConfig:
    def __init__(self):
        logger.info("Caricamento configurazione applicazione...")
        self.config = self._load_config()
        self._validate_config()
        logger.info("Configurazione caricata con successo.")
    def _load_config(self):
        return {
            'GITHUB_USER': st.secrets.get("GITHUB_USER"), 'REPO_NAME': st.secrets.get("REPO_NAME"),
            'FILE_PATH_MEDICI': st.secrets.get("FILE_PATH_MEDICI", "medici.json"),
            'GITHUB_TOKEN': st.secrets.get("GITHUB_TOKEN"),
            'MAX_RETRY_ATTEMPTS': int(st.secrets.get("MAX_RETRY_ATTEMPTS", "2")), # Ridotto per test più rapidi
            'RETRY_DELAY_SECONDS': int(st.secrets.get("RETRY_DELAY_SECONDS", "3")),
            'REQUEST_TIMEOUT': int(st.secrets.get("REQUEST_TIMEOUT", "15"))
        }
    def _validate_config(self):
        required = ['GITHUB_USER', 'REPO_NAME', 'GITHUB_TOKEN']
        missing = [f for f in required if not self.config[f]]
        if missing: raise ValueError(f"Configurazione mancante: {', '.join(missing)}")
    def get(self, key, default=None): return self.config.get(key, default)
    @property
    def api_url(self): return f"https://api.github.com/repos/{self.get('GITHUB_USER')}/{self.get('REPO_NAME')}/contents/{self.get('FILE_PATH_MEDICI')}"
    @property
    def headers(self): return {"Authorization": f"token {self.get('GITHUB_TOKEN')}", "Accept": "application/vnd.github.v3+json"}

try:
    app_config = AppConfig()
except ValueError as e_config:
    st.error(f"❌ Errore Critico di Configurazione: {e_config}"); st.error("Verifica le secrets su Streamlit Cloud."); st.stop()

# --- SESSION MANAGER (Suggerimento 8 - parziale) ---
class SessionManager:
    @staticmethod
    def init_session_vars():
        defaults = {
            'elenco_medici_completo': [], 'medici_pianificati': [],
            'df_turni': None, # Inizializza a None per distinguere da DataFrame vuoto
            'sha_medici': None,
            'selected_mese_val': datetime.now().month, 'selected_anno_val': datetime.now().year,
            'github_connection_checked': False, 'config_checked': False,
            'last_calendar_key': None # Per la cache semplificata
        }
        for key, val in defaults.items():
            if key not in st.session_state: st.session_state[key] = val
        logger.info("Variabili di sessione inizializzate/verificate.")
    @staticmethod
    def get_safe(key, default=None): return st.session_state.get(key, default)
    @staticmethod
    def set_safe(key, value): 
        try: st.session_state[key] = value; return True
        except Exception as e: logger.error(f"Errore impostazione session_state[{key}]: {e}"); return False
    @staticmethod
    def clear_calendar_related_state():
        logger.debug("Pulizia stato calendario (df_turni, last_calendar_key)")
        if 'df_turni' in st.session_state: del st.session_state.df_turni
        if 'last_calendar_key' in st.session_state: del st.session_state.last_calendar_key

SessionManager.init_session_vars() # Inizializza subito

# --- DECORATOR PERFORMANCE MONITORING (Suggerimento 7) ---
def monitor_performance(func_name_override=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.perf_counter() # Più preciso per misurazioni brevi
            try:
                result = func(*args, **kwargs)
                execution_time = time.perf_counter() - start_time
                name = func_name_override or func.__name__
                logger.info(f"⏱️ {name}: {execution_time:.4f}s")
                if execution_time > app_config.get('PERFORMANCE_THRESHOLD_WARN', 3): # Threshold configurabile (default 3s)
                    st.sidebar.caption(f"⚡ {name} lento: {execution_time:.1f}s")
                return result
            except Exception as e_perf:
                execution_time = time.perf_counter() - start_time
                logger.error(f"❌ {func_name_override or func.__name__} fallita ({execution_time:.4f}s): {e_perf}", exc_info=True)
                raise
        return wrapper
    return decorator

# --- FUNZIONI DI VALIDAZIONE (Suggerimento 5 - valida_nome_medico_v2) ---
@monitor_performance()
def valida_nome_medico_v2(nome_input, elenco_medici_corrente):
    if not isinstance(nome_input, str): return False, "Il nome deve essere una stringa."
    nome = unicodedata.normalize('NFKC', nome_input.strip())
    if not nome: return False, "Il nome non può essere vuoto."
    if len(nome) < 2: return False, "Il nome deve contenere almeno 2 caratteri."
    if len(nome) > 100: return False, "Il nome non può superare 100 caratteri."
    if not re.match(r"^[\p{L}\p{M}\s.'-]+$", nome, re.UNICODE): return False, "Il nome contiene caratteri non validi."
    existing_names_normalized = [unicodedata.normalize('NFKC', m.strip().lower()) for m in elenco_medici_corrente]
    if unicodedata.normalize('NFKC', nome.lower()) in existing_names_normalized: return False, f"Il medico '{nome}' è già presente."
    return True, nome # Restituisce il nome normalizzato

def verifica_connessione_github():
    issues = []
    logger.info("Verifica connessione GitHub...")
    try:
        res = requests.get(f"https://api.github.com/repos/{app_config.get('GITHUB_USER')}/{app_config.get('REPO_NAME')}", headers=app_config.headers, timeout=app_config.get('REQUEST_TIMEOUT'))
        if res.status_code == 404: issues.append(f"Repo '{app_config.get('GITHUB_USER')}/{app_config.get('REPO_NAME')}' non trovato.")
        elif res.status_code == 401: issues.append("Token GitHub non valido/permessi insuff.")
        elif res.status_code != 200: issues.append(f"Errore GitHub repo: {res.status_code}.")
        else: logger.info("Connessione GitHub repository OK.")
    except requests.exceptions.RequestException as e: issues.append(f"Errore connessione GitHub: {e}.")
    if issues: logger.warning(f"Problemi connessione GitHub: {issues}")
    return issues

# --- BACKUP LOCALE ---
@monitor_performance()
def salva_backup_locale(data_to_save, filename):
    try:
        backup_dir = os.path.join(tempfile.gettempdir(), "medical_shifts_app_backup")
        os.makedirs(backup_dir, exist_ok=True); backup_path = os.path.join(backup_dir, filename)
        with open(backup_path, 'w', encoding='utf-8') as f: json.dump(data_to_save, f, indent=2, ensure_ascii=False)
        logger.info(f"Backup locale salvato: {backup_path}")
    except Exception as e: logger.warning(f"Impossibile salvare backup '{filename}': {e}"); st.sidebar.caption(f"⚠️ Backup locale fallito.")
@monitor_performance()
def carica_backup_locale(filename):
    try:
        backup_dir = os.path.join(tempfile.gettempdir(), "medical_shifts_app_backup")
        backup_path = os.path.join(backup_dir, filename)
        if os.path.exists(backup_path):
            with open(backup_path, 'r', encoding='utf-8') as f: data_loaded = json.load(f)
            logger.info(f"Backup '{filename}' caricato da: {backup_path}"); return data_loaded
        else: logger.info(f"Nessun backup '{filename}' in {backup_path}")
    except Exception as e: logger.warning(f"Errore caricamento backup '{filename}': {e}")
    return None

# --- RETRY DECORATOR ---
def retry_github_api(max_retries_override=None, delay_seconds_override=None): # Permette override da config
    actual_max_retries = max_retries_override if max_retries_override is not None else app_config.get('MAX_RETRY_ATTEMPTS')
    actual_delay_seconds = delay_seconds_override if delay_seconds_override is not None else app_config.get('RETRY_DELAY_SECONDS')
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(actual_max_retries):
                try: return func(*args, **kwargs)
                except requests.exceptions.HTTPError as e:
                    last_exception = e
                    if e.response.status_code in [404, 401]: logger.error(f"Errore HTTP GitHub non recuperabile ({e.response.status_code}) per {func.__name__}."); raise 
                    retry_after = int(e.response.headers.get("Retry-After", actual_delay_seconds * (2 ** attempt)))
                    log_msg = f"Rate limit" if e.response.status_code == 403 else f"Errore server ({e.response.status_code})"
                    if attempt < actual_max_retries - 1:
                        logger.warning(f"{log_msg} GitHub per {func.__name__}. Tentativo {attempt + 1}/{actual_max_retries}. Riprovo tra {retry_after}s...")
                        st.sidebar.caption(f"⏳ {log_msg}. Riprovo tra {retry_after}s...")
                        time.sleep(retry_after)
                    else: logger.error(f"Errore HTTP GitHub finale ({e.response.status_code}) per {func.__name__}: {e.response.text}"); raise
                except requests.exceptions.RequestException as e:
                    last_exception = e
                    if attempt < actual_max_retries - 1:
                        wait_time = actual_delay_seconds * (2 ** attempt)
                        logger.warning(f"Errore connessione per {func.__name__}. Tentativo {attempt + 1}/{actual_max_retries}. Riprovo tra {wait_time}s... Errore: {e}")
                        st.sidebar.caption(f"⏳ Errore connessione. Riprovo tra {wait_time}s...")
                        time.sleep(wait_time)
                    else: logger.error(f"Errore connessione finale per {func.__name__} dopo {actual_max_retries} tentativi: {e}"); raise
            if last_exception: raise last_exception
            return None # Non dovrebbe essere raggiunto
        return wrapper
    return decorator

# --- FUNZIONI GITHUB ---
@retry_github_api()
@monitor_performance("Caricamento Medici GitHub")
def carica_medici_da_github():
    logger.info(f"Caricamento medici da GitHub: {app_config.api_url}")
    res = requests.get(app_config.api_url, headers=app_config.headers, timeout=app_config.get('REQUEST_TIMEOUT'))
    res.raise_for_status()
    contenuto = res.json();
    if "content" not in contenuto or "sha" not in contenuto: logger.error("Risposta GitHub malformata."); raise ValueError("Formato risposta GitHub inatteso.")
    file_sha = contenuto["sha"]; elenco_json = base64.b64decode(contenuto["content"]).decode('utf-8'); elenco = json.loads(elenco_json)
    SessionManager.set_safe('sha_medici', file_sha)
    logger.info(f"Medici caricati da GitHub ({len(elenco)}). SHA: {file_sha[:7]}...")
    return elenco

@monitor_performance("Inizializzazione Elenco Medici")
def inizializza_elenco_medici():
    try:
        elenco = carica_medici_da_github()
        salva_backup_locale(elenco, MEDICI_BACKUP_FILE)
        return elenco
    except requests.exceptions.HTTPError as e_gh:
        if e_gh.response.status_code == 404: st.sidebar.warning(f"File medici non trovato su GitHub."); logger.info("File medici 404, init vuoto."); SessionManager.set_safe('sha_medici', None); return []
        else: logger.error(f"Errore HTTP GitHub ({e_gh.response.status_code}). Tento backup."); st.sidebar.error("⚠️ Errore GitHub caricamento medici.")
    except requests.exceptions.RequestException as e_req: logger.error(f"Errore rete GitHub ({e_req}). Tento backup."); st.sidebar.error("⚠️ Errore rete caricamento medici.")
    except Exception as e_gen: logger.error(f"Errore imprevisto caricamento medici: {e_gen}", exc_info=True); st.sidebar.error(f"⚠️ Errore: {e_gen}")
    backup_data = carica_backup_locale(MEDICI_BACKUP_FILE)
    if backup_data is not None: st.sidebar.warning("Medici caricati da backup locale."); return backup_data
    else: st.sidebar.error("Impossibile caricare medici."); return []

@retry_github_api()
@monitor_performance("Salvataggio Medici GitHub")
def salva_medici_su_github(lista_medici, sha_corrente): # Gestione eccezioni più specifica (Suggerimento 4)
    if not isinstance(lista_medici, list): raise TypeError("lista_medici deve essere una lista.")
    try: blob = json.dumps(lista_medici, indent=2, ensure_ascii=False).encode('utf-8'); encoded_content = base64.b64encode(blob).decode('utf-8')
    except (TypeError, ValueError) as e_json_ser: logger.error(f"Errore encoding JSON: {e_json_ser}"); raise ValueError(f"Impossibile serializzare lista medici: {e_json_ser}")
    data = {"message": f"Agg. elenco medici - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "content": encoded_content, "branch": "main"}
    if sha_corrente: data["sha"] = sha_corrente
    try:
        logger.info(f"Salvataggio {len(lista_medici)} medici su GitHub. SHA: {str(sha_corrente)[:7]}...")
        res = requests.put(app_config.api_url, headers=app_config.headers, json=data, timeout=app_config.get('REQUEST_TIMEOUT'))
        res.raise_for_status()
        if res.status_code in [200, 201]:
            nuovo_sha = res.json()["content"]["sha"]; SessionManager.set_safe('sha_medici', nuovo_sha)
            logger.info(f"Medici salvati. Nuovo SHA: {nuovo_sha[:7]}..."); salva_backup_locale(lista_medici, MEDICI_BACKUP_FILE); return True
        else: logger.warning(f"Salvataggio parziale: status {res.status_code}"); return False
    except requests.exceptions.Timeout: logger.error("Timeout salvataggio GitHub"); st.sidebar.error("⏰ Timeout connessione GitHub"); return False
    except requests.exceptions.ConnectionError: logger.error("Errore connessione GitHub"); st.sidebar.error("🌐 Problema connessione GitHub"); return False
    except requests.exceptions.HTTPError as e_http_put: 
        logger.error(f"Errore HTTP GitHub salvataggio: {e_http_put.response.status_code} - {e_http_put.response.text}")
        st.sidebar.error(f"❌ Errore GitHub salvataggio: {e_http_put.response.status_code}")
        # Se è un errore di conflitto SHA (409), suggerisci un refresh
        if e_http_put.response.status_code == 409:
            st.sidebar.warning("Conflitto di versione rilevato. Prova a ricaricare la pagina e riapplicare le modifiche.")
            SessionManager.set_safe('sha_medici', None) # Invalida SHA locale
        return False

# --- VERIFICA CONNESSIONE GITHUB ALL'AVVIO ---
if not SessionManager.get_safe('github_connection_checked'):
    conn_issues = verifica_connessione_github()
    if conn_issues: st.sidebar.error("⚠️ **Problemi Connessione GitHub:**"); [st.sidebar.error(f"  • {issue}") for issue in conn_issues]
    SessionManager.set_safe('github_connection_checked', True)

# --- INIZIALIZZAZIONE STATO ---
if not SessionManager.get_safe('elenco_medici_completo'): # Se è lista vuota, prova a inizializzare
    with st.spinner("Caricamento elenco medici..."): SessionManager.set_safe('elenco_medici_completo', inizializza_elenco_medici())
# SHA dovrebbe essere già gestito da inizializza_elenco_medici

# --- FUNZIONI CALENDARIO (Cache Suggerimento 3, Ottimizzazione Suggerimento 6) ---
@st.cache_data(ttl=3600, show_spinner="Generazione calendario...")
@monitor_performance("Generazione Calendario (Cached)")
def genera_calendario_cached(anno: int, mese: int, medici_lista_input: list): # Accetta lista direttamente
    try:
        medici_sorted = sorted(list(set(medici_lista_input))) # Normalizza per la logica interna se necessario, ma la cache usa gli argomenti come sono
        logger.info(f"Cache miss o uso diretto: Genero calendario {mese}/{anno}, {len(medici_sorted)} medici")
        return genera_struttura_calendario(anno, mese, medici_sorted)
    except Exception as e: logger.error(f"Errore gen. calendario (cached): {e}", exc_info=True); st.error(f"Impossibile generare calendario: {e}"); return pd.DataFrame()

@monitor_performance("Creazione Struttura Calendario")
def genera_struttura_calendario(anno, mese, medici_selezionati):
    try:
        start_date = datetime(anno, mese, 1); _, ultimo_giorno = calendar.monthrange(anno, mese); end_date = datetime(anno, mese, ultimo_giorno)
        date_range_pd = pd.date_range(start=start_date, end=end_date, freq='D')
        try: festivita_anno = holidays.country_holidays("IT", years=anno)
        except Exception as e_hol: logger.warning(f"Festività {anno} non disponibili: {e_hol}"); st.sidebar.caption(f"⚠️ Festività {anno} non caricate."); festivita_anno = {}
        data_dict = {COL_DATA: date_range_pd, COL_GIORNO: [d.strftime("%A") for d in date_range_pd],
                     COL_FESTIVO: [d.date() in festivita_anno for d in date_range_pd],
                     COL_NOME_FESTIVO: [festivita_anno.get(d.date(), "") for d in date_range_pd]}
        for medico in medici_selezionati: data_dict[medico] = "Presente"
        df = pd.DataFrame(data_dict)
        logger.info(f"Calendario {mese}/{anno} creato: {len(df)} gg, {len(medici_selezionati)} medici.")
        return df
    except Exception as e: logger.error(f"Errore grave gen. struttura calendario: {e}", exc_info=True); st.error(f"Errore critico gen. calendario: {e}"); return pd.DataFrame()

@monitor_performance("Aggiornamento Calendario")
def aggiorna_calendario_se_necessario(anno, mese, medici_pianificati_lista):
    try:
        # Crea una chiave basata su frozenset per la cache (Suggerimento 3)
        medici_set_frozen = frozenset(medici_pianificati_lista)
        current_key = f"{anno}-{mese}-{hash(medici_set_frozen)}" # Hash del frozenset è stabile
        
        if (SessionManager.get_safe('last_calendar_key') != current_key or 
            SessionManager.get_safe('df_turni') is None): # Controlla se df_turni è None
            if medici_pianificati_lista: 
                # Passa la lista direttamente, @st.cache_data gestirà l'hashing degli argomenti (liste sono unhashable, quindi le converte internamente o dà errore se non gestito)
                # Per @st.cache_data, è meglio passare tuple invece di liste se il contenuto è lo stesso.
                # O usare la versione con stringa serializzata come prima se questa dà problemi.
                # Per semplicità, provo con la lista, st.cache_data dovrebbe gestirla o avvisare.
                # Se dà errore, tornare a passare json.dumps(sorted(list(set(medici_pianificati_lista)))).
                SessionManager.set_safe('df_turni', genera_calendario_cached(anno, mese, tuple(sorted(list(set(medici_pianificati_lista)))))) # Passa una tupla ordinata
            else: SessionManager.set_safe('df_turni', pd.DataFrame())
            SessionManager.set_safe('last_calendar_key', current_key)
            # Log spostati
    except Exception as e: logger.error(f"Errore critico aggiornamento calendario: {e}", exc_info=True); st.error(f"Impossibile aggiornare calendario: {e}"); SessionManager.set_safe('df_turni', pd.DataFrame())


# --- EXPORT EXCEL ---
@monitor_performance("Generazione Export Excel")
def esporta_con_formattazione(df_originale, nome_file_base):
    # ... (come prima, ma usando le COSTANTI per dimensioni)
    if df_originale is None or df_originale.empty: logger.warning("Export DataFrame vuoto/nullo."); st.error("Nessun dato da esportare."); return None
    try:
        output = BytesIO(); df_export = df_originale.copy()
        if COL_DATA in df_export.columns:
            if not pd.api.types.is_datetime64_any_dtype(df_export[COL_DATA]):
                try: df_export[COL_DATA] = pd.to_datetime(df_export[COL_DATA])
                except Exception as e_conv: logger.warning(f"Impossibile convertire '{COL_DATA}' a datetime per Excel: {e_conv}")
            if pd.api.types.is_datetime64_any_dtype(df_export[COL_DATA]): df_export[COL_DATA] = df_export[COL_DATA].dt.date
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_export.to_excel(writer, index=False, sheet_name='Turni'); worksheet = writer.sheets['Turni']
            header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
            header_font = Font(color="FFFFFF", bold=True, name='Calibri', size=11); cell_font = Font(name='Calibri', size=10)
            center_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True); left_alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
            for col_idx, _ in enumerate(df_export.columns, 1):
                cell = worksheet.cell(row=1, column=col_idx); cell.fill = header_fill; cell.font = header_font; cell.alignment = center_alignment
            idx_col_giorno = df_export.columns.get_loc(COL_GIORNO) + 1 if COL_GIORNO in df_export.columns else -1
            idx_col_nome_festivo = df_export.columns.get_loc(COL_NOME_FESTIVO) + 1 if COL_NOME_FESTIVO in df_export.columns else -1
            for row_idx, row_data in enumerate(worksheet.iter_rows(min_row=2, max_col=len(df_export.columns), max_row=worksheet.max_row), 2):
                for col_num_excel, cell in enumerate(row_data, 1):
                    cell.font = cell_font
                    if isinstance(cell.value, (datetime, pd.Timestamp, date)): cell.number_format = 'DD/MM/YYYY'; cell.alignment = center_alignment
                    elif col_num_excel == idx_col_giorno: cell.alignment = left_alignment
                    elif col_num_excel == idx_col_nome_festivo: cell.alignment = left_alignment
                    else: cell.alignment = center_alignment
            for column_cells in worksheet.columns:
                max_len = 0; column_letter = column_cells[0].column_letter
                for cell in column_cells:
                    try:
                        if cell.value:
                            val_to_measure = cell.value.strftime('%d/%m/%Y') if isinstance(cell.value, (datetime, pd.Timestamp, date)) else str(cell.value)
                            max_len = max(max_len, len(val_to_measure))
                    except: pass
                worksheet.column_dimensions[column_letter].width = min(max(max_len + COLUMN_PADDING_EXCEL, MIN_COLUMN_WIDTH_EXCEL), MAX_COLUMN_WIDTH_EXCEL)
        output.seek(0); logger.info(f"Export Excel '{nome_file_base}' generato."); return output
    except Exception as e: logger.error(f"Errore gen. export Excel '{nome_file_base}': {e}", exc_info=True); st.error(f"Errore gen. file Excel: {e}"); return None

# --- UI SIDEBAR ---
st.sidebar.title("🗓️ Gestione Turni")
st.sidebar.markdown("App per la pianificazione dei turni medici.")
st.sidebar.divider(); st.sidebar.header("👨‍⚕️ Medici")
with st.sidebar.form("form_aggiungi_medico", clear_on_submit=True):
    nuovo_medico_input = st.text_input("➕ Nome nuovo medico (es. Rossi Mario)").strip()
    submitted_add = st.form_submit_button("Aggiungi Medico", type="primary")
if submitted_add and nuovo_medico_input:
    valido, msg_o_nome_norm = valida_nome_medico_v2(nuovo_medico_input, SessionManager.get_safe('elenco_medici_completo', []))
    if not valido: st.sidebar.error(msg_o_nome_norm)
    else: # msg_o_nome_norm è il nome normalizzato se valido
        elenco_aggiornato = SessionManager.get_safe('elenco_medici_completo', []) + [msg_o_nome_norm]; elenco_aggiornato.sort()
        with st.spinner("Salvataggio medico..."):
            try:
                if salva_medici_su_github(elenco_aggiornato, SessionManager.get_safe("sha_medici")):
                    SessionManager.set_safe('elenco_medici_completo', elenco_aggiornato)
                    st.toast(f"Medico '{msg_o_nome_norm}' aggiunto!", icon="✅"); logger.info(f"Medico aggiunto GitHub: {msg_o_nome_norm}"); st.rerun()
                # else: l'errore è gestito da salva_medici_su_github
            except Exception as e_save: logger.error(f"Eccezione salvataggio medico: {e_save}", exc_info=True); st.sidebar.error(f"❌ Errore critico salvataggio: {e_save}")
elenco_medici_corrente = SessionManager.get_safe('elenco_medici_completo', [])
if elenco_medici_corrente:
    options_rimuovi = ["--- Seleziona per rimuovere ---"] + sorted(list(set(elenco_medici_corrente)))
    current_sel_rimuovi = SessionManager.get_safe("medico_da_rimuovere_selection", options_rimuovi[0])
    try: default_idx_rimuovi = options_rimuovi.index(current_sel_rimuovi)
    except ValueError: default_idx_rimuovi = 0
    medico_da_rimuovere = st.sidebar.selectbox("🗑️ Rimuovi medico", options_rimuovi, index=default_idx_rimuovi, key="sel_rimuovi_medico")
    SessionManager.set_safe("medico_da_rimuovere_selection", medico_da_rimuovere)
    if medico_da_rimuovere != options_rimuovi[0] and st.sidebar.button("Conferma Rimozione", key="btn_rimuovi_medico", type="secondary"):
        medici_temp = elenco_medici_corrente.copy(); medici_temp.remove(medico_da_rimuovere)
        with st.spinner(f"Rimozione '{medico_da_rimuovere}'..."):
            try:
                if salva_medici_su_github(medici_temp, SessionManager.get_safe("sha_medici")):
                    SessionManager.set_safe('elenco_medici_completo', medici_temp)
                    st.toast(f"Medico '{medico_da_rimuovere}' rimosso.", icon="🗑️"); logger.info(f"Medico rimosso GitHub: {medico_da_rimuovere}")
                    # Pulisce lo stato del calendario e dei medici pianificati correlati
                    SessionManager.clear_calendar_related_state()
                    current_medici_pianificati = SessionManager.get_safe('medici_pianificati', [])
                    if medico_da_rimuovere in current_medici_pianificati: current_medici_pianificati.remove(medico_da_rimuovere); SessionManager.set_safe('medici_pianificati', current_medici_pianificati)
                    SessionManager.set_safe("medico_da_rimuovere_selection", options_rimuovi[0]); st.rerun()
                # else: l'errore è gestito
            except Exception as e_remove: logger.error(f"Eccezione rimozione medico: {e_remove}", exc_info=True); st.sidebar.error(f"❌ Errore critico rimozione: {e_remove}")
else: st.sidebar.caption("Nessun medico nell'elenco.")
st.sidebar.divider(); st.sidebar.header("🎯 Pianificazione")
default_medici_pianif = SessionManager.get_safe('medici_pianificati', [])
valid_default_medici = [m for m in default_medici_pianif if m in elenco_medici_corrente]
if not valid_default_medici and elenco_medici_corrente: valid_default_medici = elenco_medici_corrente[:]
medici_pianificati = st.sidebar.multiselect("👨‍⚕️ Medici da includere:", options=sorted(list(set(elenco_medici_corrente))), default=valid_default_medici, key="multi_medici_pianif", help="Seleziona medici per il calendario.")
st.sidebar.header("🗓️ Periodo")
oggi = datetime.today(); anni_disponibili = list(range(oggi.year - 3, oggi.year + 4))
idx_mese_default = SessionManager.get_safe('selected_mese_index', oggi.month - 1); idx_mese_default = oggi.month - 1 if not 0 <= idx_mese_default < 12 else idx_mese_default
idx_anno_default = SessionManager.get_safe('selected_anno_index', anni_disponibili.index(oggi.year)); idx_anno_default = anni_disponibili.index(oggi.year) if not 0 <= idx_anno_default < len(anni_disponibili) else idx_anno_default
col1_sb, col2_sb = st.sidebar.columns(2); lista_mesi = list(range(1, 13))
selected_mese = col1_sb.selectbox("Mese:", lista_mesi, index=idx_mese_default, format_func=lambda x: calendar.month_name[x], key="sel_mese")
selected_anno = col2_sb.selectbox("Anno:", anni_disponibili, index=idx_anno_default, key="sel_anno")

# --- LOGICA DI AGGIORNAMENTO PRINCIPALE ---
if SessionManager.get_safe('medici_pianificati', []) != medici_pianificati: # Confronta con il valore precedente da session_state
    SessionManager.set_safe('medici_pianificati', medici_pianificati)
    aggiorna_calendario_se_necessario(selected_anno, selected_mese, medici_pianificati) 
if (SessionManager.get_safe('selected_mese_val') != selected_mese or SessionManager.get_safe('selected_anno_val') != selected_anno):
    SessionManager.set_safe('selected_mese_val',selected_mese); SessionManager.set_safe('selected_anno_val',selected_anno)
    SessionManager.set_safe('selected_mese_index', lista_mesi.index(selected_mese)); SessionManager.set_safe('selected_anno_index', anni_disponibili.index(selected_anno))
    aggiorna_calendario_se_necessario(selected_anno, selected_mese, medici_pianificati)
elif SessionManager.get_safe('df_turni') is None: # Se il df non esiste, crealo
     aggiorna_calendario_se_necessario(selected_anno, selected_mese, medici_pianificati)
nome_mese_corrente = calendar.month_name[selected_mese]

# --- FUNZIONE DI STYLING (Suggerimento 1) ---
def evidenzia_weekend_festivi(row_series):
    try:
        data_val = row_series[COL_DATA]
        if pd.isna(data_val): return [''] * len(row_series)
        if isinstance(data_val, str):
            try: data_val = pd.to_datetime(data_val)
            except (ValueError, TypeError): logger.warning(f"Impossibile parsare data: {data_val}"); return [''] * len(row_series)
        elif isinstance(data_val, date) and not isinstance(data_val, datetime): data_val = datetime.combine(data_val, datetime.min.time())
        if not hasattr(data_val, 'weekday'): logger.warning(f"Oggetto data senza weekday: {type(data_val)}"); return [''] * len(row_series)
        is_weekend = data_val.weekday() >= 5; is_festivo = row_series.get(COL_FESTIVO, False) # Usa .get per sicurezza
        if is_weekend or is_festivo: return ['background-color: #e9ecef; font-weight: 500;'] * len(row_series)
        else: return [''] * len(row_series)
    except Exception as e_style: logger.error(f"Errore styling riga: {e_style}", exc_info=True); return [''] * len(row_series)

# --- AREA PRINCIPALE ---
st.title(f"🗓️ Pianificazione Turni Medici")
st.markdown(f"### {nome_mese_corrente} {selected_anno}")
df_turni_corrente = SessionManager.get_safe('df_turni')
if not medici_pianificati: st.info("👈 **Nessun medico selezionato.** Scegli dalla sidebar.")
elif df_turni_corrente is None or df_turni_corrente.empty:
    st.warning("📅 Calendario vuoto. Verifica selezioni o ricarica."); logger.warning(f"df_turni vuoto/None. Medici: {len(medici_pianificati)}. Stato: {df_turni_corrente}")
else:
    st.markdown("#### ✨ **Visualizzazione Calendario**")
    df_visualizzazione = df_turni_corrente.copy()
    try:
        styled_df = df_visualizzazione.style.apply(evidenzia_weekend_festivi, axis=1).format({COL_DATA: lambda dt: dt.strftime('%d/%m/%Y (%a)') if pd.notna(dt) else ""})
        styled_df = styled_df.hide([COL_FESTIVO], axis='columns')
        st.dataframe(styled_df, use_container_width=True, hide_index=True, height=(len(df_visualizzazione) + 1) * ROW_HEIGHT_PX + TABLE_PADDING_PX)
    except Exception as e_df_display: logger.error(f"Errore display DataFrame stilizzato: {e_df_display}", exc_info=True); st.error("⚠️ Errore visualizzazione calendario."); st.dataframe(df_visualizzazione, use_container_width=True, hide_index=True)
    st.divider()
    st.markdown("#### 📝 **Inserisci/Modifica Assenze**")
    cols_per_editor = [COL_DATA, COL_GIORNO] + medici_pianificati
    column_config_editor = {COL_DATA: st.column_config.DateColumn("Data", format="DD/MM/YYYY", disabled=True, width="small"),
                            COL_GIORNO: st.column_config.TextColumn("Giorno", disabled=True, width="small")}
    for medico in medici_pianificati:
        nome_cognome = medico.split(); nome_display = nome_cognome[-1].capitalize() if len(nome_cognome) > 1 else medico.capitalize()
        column_config_editor[medico] = st.column_config.SelectboxColumn(f"Dr. {nome_display}", help=f"Stato per {medico}", options=TIPI_ASSENZA, required=True, width="medium")
    df_editor_input_valid = all(col_edit in df_turni_corrente.columns for col_edit in cols_per_editor)
    if not df_editor_input_valid:
        missing_cols = [c for c in cols_per_editor if c not in df_turni_corrente.columns]
        logger.error(f"Colonne mancanti in df_turni per editor: {missing_cols}. Disponibili: {df_turni_corrente.columns.tolist()}")
        st.error(f"Errore interno: colonne {missing_cols} non trovate per editor.")
    else:
        df_editor_input = df_turni_corrente[cols_per_editor].copy()
        editor_key = f"data_editor_assenze_{SessionManager.get_safe('last_calendar_key', 'default_key')}"
        try:
            edited_df_assenze = st.data_editor(df_editor_input, column_config=column_config_editor, use_container_width=True, hide_index=True, num_rows="fixed", key=editor_key, height=(len(df_editor_input) + 1) * ROW_HEIGHT_PX + TABLE_PADDING_PX)
            modifiche_editor = False
            for medico_col in medici_pianificati:
                if df_turni_corrente[medico_col].dtype == edited_df_assenze[medico_col].dtype and not df_turni_corrente[medico_col].equals(edited_df_assenze[medico_col]):
                    # Aggiorna direttamente il DataFrame in session_state
                    SessionManager.get_safe('df_turni')[medico_col] = edited_df_assenze[medico_col].copy(); modifiche_editor = True
            if modifiche_editor: st.toast("Assenze aggiornate localmente.", icon="📝"); logger.info("Assenze modificate e aggiornate in session_state.")
        except Exception as e_data_editor: logger.error(f"Errore st.data_editor: {e_data_editor}", exc_info=True); st.error("⚠️ Errore editor assenze. Ricarica.")
    st.divider()
    st.markdown("#### 📤 **Esporta Calendario**")
    if df_turni_corrente is not None and not df_turni_corrente.empty:
        col_export_btn, col_export_info = st.columns([0.3, 0.7])
        nome_file_excel = f"Turni_{nome_mese_corrente.replace(' ', '_')}_{selected_anno}.xlsx"
        with col_export_btn:
            excel_export_data = esporta_con_formattazione(df_turni_corrente.copy(), nome_file_excel)
            if excel_export_data:
                st.download_button(label="📥 Scarica Excel", data=excel_export_data, file_name=nome_file_excel, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="download_excel_btn", help="Scarica calendario con stili.")
            # else: l'errore è già mostrato
        with col_export_info: st.caption(f"File '{nome_file_excel}'. Include formattazione.")
    else: st.caption("Nessun dato da esportare.")

st.sidebar.divider()
st.sidebar.markdown(f"""<div style="font-size: 0.8em; text-align: center; color: grey;">
    Gestione Turni Medici v1.1<br>
    {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</div>""", unsafe_allow_html=True)
logger.info("--- Rendering pagina completato ---")
