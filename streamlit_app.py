import streamlit as st
import pandas as pd
import calendar
from datetime import datetime, date # IMPORT ESPLICITO
# holidays non è più strettamente necessario se non visualizziamo festivi, ma lo tengo per ora per la generazione base
import holidays 
# from io import BytesIO # Non più necessario per Excel
import requests
import json
import base64
import time
import logging
import tempfile
import os
from functools import wraps
# from openpyxl.styles import PatternFill, Font, Alignment # Non più necessario per Excel
import unicodedata 
import re
import hashlib
import regex

# --- CONFIGURAZIONE LOGGING --- (come prima)
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
logger.info("--- Applicazione Gestione Turni Medici (Solo Input Assenze) AVVIATA ---")

# --- CONFIGURAZIONE INIZIALE STREAMLIT ---
st.set_page_config(page_title="Input Assenze Medici", layout="wide", initial_sidebar_state="expanded")

# --- COSTANTI ---
COL_DATA = "Data"
COL_GIORNO = "Giorno"
COL_FESTIVO = "Festivo"       # <-- DECOMMENTA QUESTA
COL_NOME_FESTIVO = "Nome Festivo" # <-- DECOMMENTA QUESTA
TIPI_ASSENZA = ["Presente", "Ferie", "Malattia", "Congresso", "Lezione", "Altro"]
MEDICI_BACKUP_FILE = "medici_backup.json"
ASSENZE_FILE_PREFIX = "assenze_medici"

ROW_HEIGHT_PX = 35
TABLE_PADDING_PX = 3
MIN_COLUMN_WIDTH_EXCEL = 12 # Non più usato se non c'è export Excel
MAX_COLUMN_WIDTH_EXCEL = 45 # Non più usato se non c'è export Excel
COLUMN_PADDING_EXCEL = 3    # Non più usato se non c'è export Excel

# --- CONFIGURAZIONE APPLICAZIONE --- (come prima)
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
            'MAX_RETRY_ATTEMPTS': int(st.secrets.get("MAX_RETRY_ATTEMPTS", "2")),
            'RETRY_DELAY_SECONDS': int(st.secrets.get("RETRY_DELAY_SECONDS", "3")),
            'REQUEST_TIMEOUT': int(st.secrets.get("REQUEST_TIMEOUT", "15")),
            'ASSENZE_BRANCH': st.secrets.get("ASSENZE_BRANCH", "main") # Branch per salvare le assenze
        }
    def _validate_config(self):
        required = ['GITHUB_USER', 'REPO_NAME', 'GITHUB_TOKEN']
        missing = [f for f in required if not self.config[f]]
        if missing: raise ValueError(f"Configurazione mancante: {', '.join(missing)}")
    def get(self, key, default=None): return self.config.get(key, default)
    @property
    def medici_api_url(self): return f"https://api.github.com/repos/{self.get('GITHUB_USER')}/{self.get('REPO_NAME')}/contents/{self.get('FILE_PATH_MEDICI')}"
    def assenze_api_url(self, file_path_assenze): return f"https://api.github.com/repos/{self.get('GITHUB_USER')}/{self.get('REPO_NAME')}/contents/{file_path_assenze}"
    @property
    def headers(self): return {"Authorization": f"token {self.get('GITHUB_TOKEN')}", "Accept": "application/vnd.github.v3+json"}

try:
    app_config = AppConfig()
except ValueError as e_config:
    st.error(f"❌ Errore Critico di Configurazione: {e_config}"); st.error("Verifica le secrets."); st.stop()

# --- SESSION MANAGER --- (come prima)
class SessionManager:
    @staticmethod
    def init_session_vars():
        defaults = {
            'elenco_medici_completo': [], 'medici_pianificati': [], 'df_turni': None, 
            'sha_medici': None, 'sha_assenze': {}, # SHA per file assenze, indicizzato per file_path
            'selected_mese_val': datetime.now().month, 'selected_anno_val': datetime.now().year,
            'github_connection_checked': False, 'config_checked': False, 'last_calendar_key': None
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
        SessionManager.set_safe('df_turni', None) # Imposta a None invece di cancellare
        SessionManager.set_safe('last_calendar_key', None)

SessionManager.init_session_vars()

# --- DECORATORS (monitor_performance, retry_github_api) --- (come prima, li ometto per brevità)
def monitor_performance(func_name_override=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.perf_counter(); result = func(*args, **kwargs); execution_time = time.perf_counter() - start_time
            name = func_name_override or func.__name__; logger.info(f"⏱️ {name}: {execution_time:.4f}s")
            if execution_time > app_config.get('PERFORMANCE_THRESHOLD_WARN', 3): st.sidebar.caption(f"⚡ {name} lento: {execution_time:.1f}s")
            return result
        return wrapper
    return decorator

def retry_github_api(max_retries_override=None, delay_seconds_override=None):
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
            return None
        return wrapper
    return decorator

# --- FUNZIONI DI VALIDAZIONE (valida_nome_medico_v2, verifica_connessione_github) --- (come prima, omesse per brevità)
@monitor_performance()
def valida_nome_medico_v2(nome_input, elenco_medici_corrente):
    if not isinstance(nome_input, str): return False, "Il nome deve essere una stringa."
    nome = unicodedata.normalize('NFKC', nome_input.strip())
    if not nome: return False, "Il nome non può essere vuoto."
    if len(nome) < 2: return False, "Il nome deve contenere almeno 2 caratteri."
    if len(nome) > 100: return False, "Il nome non può superare 100 caratteri."
    if not regex.match(r"^[\p{L}\p{M}\s.'-]+$", nome): return False, "Il nome contiene caratteri non validi."
    existing_names_normalized = [unicodedata.normalize('NFKC', m.strip().lower()) for m in elenco_medici_corrente]
    if unicodedata.normalize('NFKC', nome.lower()) in existing_names_normalized: return False, f"Il medico '{nome}' è già presente."
    return True, nome

def verifica_connessione_github():
    issues = []; logger.info("Verifica connessione GitHub...")
    try:
        res = requests.get(f"https://api.github.com/repos/{app_config.get('GITHUB_USER')}/{app_config.get('REPO_NAME')}", headers=app_config.headers, timeout=app_config.get('REQUEST_TIMEOUT'))
        if res.status_code == 404: issues.append(f"Repo '{app_config.get('GITHUB_USER')}/{app_config.get('REPO_NAME')}' non trovato.")
        elif res.status_code == 401: issues.append("Token GitHub non valido/permessi insuff.")
        elif res.status_code != 200: issues.append(f"Errore GitHub repo: {res.status_code}.")
        else: logger.info("Connessione GitHub repository OK.")
    except requests.exceptions.RequestException as e: issues.append(f"Errore connessione GitHub: {e}.")
    if issues: logger.warning(f"Problemi connessione GitHub: {issues}")
    return issues

# --- BACKUP LOCALE --- (come prima, omesse per brevità)
@monitor_performance()
def salva_backup_locale(data_to_save, filename):
    try:
        backup_dir = os.path.join(tempfile.gettempdir(), "medical_shifts_app_backup"); os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, filename)
        with open(backup_path, 'w', encoding='utf-8') as f: json.dump(data_to_save, f, indent=2, ensure_ascii=False)
        logger.info(f"Backup locale salvato: {backup_path}")
    except Exception as e: logger.warning(f"Impossibile salvare backup '{filename}': {e}"); st.sidebar.caption(f"⚠️ Backup locale fallito.")
@monitor_performance()
def carica_backup_locale(filename):
    try:
        backup_dir = os.path.join(tempfile.gettempdir(), "medical_shifts_app_backup"); backup_path = os.path.join(backup_dir, filename)
        if os.path.exists(backup_path):
            with open(backup_path, 'r', encoding='utf-8') as f: data_loaded = json.load(f)
            logger.info(f"Backup '{filename}' caricato da: {backup_path}"); return data_loaded
        else: logger.info(f"Nessun backup '{filename}' in {backup_path}")
    except Exception as e: logger.warning(f"Errore caricamento backup '{filename}': {e}")
    return None

# --- FUNZIONI GITHUB (carica_medici, salva_medici) --- (come prima, omesse per brevità)
@retry_github_api()
@monitor_performance("Caricamento Medici GitHub")
def carica_medici_da_github():
    logger.info(f"Caricamento medici da GitHub: {app_config.medici_api_url}")
    res = requests.get(app_config.medici_api_url, headers=app_config.headers, timeout=app_config.get('REQUEST_TIMEOUT'))
    res.raise_for_status(); contenuto = res.json();
    if "content" not in contenuto or "sha" not in contenuto: logger.error("Risposta GitHub malformata."); raise ValueError("Formato risposta GitHub inatteso.")
    file_sha = contenuto["sha"]; elenco_json = base64.b64decode(contenuto["content"]).decode('utf-8'); elenco = json.loads(elenco_json)
    SessionManager.set_safe('sha_medici', file_sha); logger.info(f"Medici caricati da GitHub ({len(elenco)}). SHA: {file_sha[:7]}..."); return elenco

@monitor_performance("Inizializzazione Elenco Medici")
def inizializza_elenco_medici():
    try: elenco = carica_medici_da_github(); salva_backup_locale(elenco, MEDICI_BACKUP_FILE); return elenco
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
def salva_medici_su_github(lista_medici, sha_corrente):
    if not isinstance(lista_medici, list): raise TypeError("lista_medici deve essere una lista.")
    try: blob = json.dumps(lista_medici, indent=2, ensure_ascii=False).encode('utf-8'); encoded_content = base64.b64encode(blob).decode('utf-8')
    except (TypeError, ValueError) as e_json_ser: logger.error(f"Errore encoding JSON: {e_json_ser}"); raise ValueError(f"Impossibile serializzare lista medici: {e_json_ser}")
    data = {"message": f"Agg. elenco medici - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "content": encoded_content, "branch": app_config.get('ASSENZE_BRANCH')}
    if sha_corrente: data["sha"] = sha_corrente
    try:
        logger.info(f"Salvataggio {len(lista_medici)} medici su GitHub. SHA: {str(sha_corrente)[:7]}...")
        res = requests.put(app_config.medici_api_url, headers=app_config.headers, json=data, timeout=app_config.get('REQUEST_TIMEOUT'))
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
        if e_http_put.response.status_code == 409: st.sidebar.warning("Conflitto versione. Ricarica e riprova."); SessionManager.set_safe('sha_medici', None)
        return False

# --- NUOVA FUNZIONE PER SALVARE/CARICARE FILE JSON GENERICO SU GITHUB ---
@retry_github_api()
@monitor_performance("Operazione File JSON GitHub")
def opera_su_file_json_github(file_path_in_repo, dati_da_salvare=None, sha_corrente=None, operazione="salva"):
    """
    Esegue operazioni (salva, carica, controlla esistenza) su un file JSON in un repository GitHub.
    Per 'salva': `dati_da_salvare` è obbligatorio.
    Per 'carica' o 'controlla': `dati_da_salvare` è ignorato.
    Restituisce i dati caricati e il nuovo SHA per 'carica', (True/False, nuovo_SHA) per 'salva', (True/False, sha) per 'controlla'.
    """
    target_api_url = app_config.assenze_api_url(file_path_in_repo) # URL specifico per il file
    
    if operazione == "salva":
        if dati_da_salvare is None: raise ValueError("`dati_da_salvare` obbligatori per operazione 'salva'.")
        try: blob = json.dumps(dati_da_salvare, indent=2, ensure_ascii=False).encode('utf-8'); encoded_content = base64.b64encode(blob).decode('utf-8')
        except (TypeError, ValueError) as e_json: logger.error(f"Errore encoding JSON per '{file_path_in_repo}': {e_json}"); raise
        
        payload = {"message": f"Aggiornamento file {file_path_in_repo} - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                     "content": encoded_content, "branch": app_config.get('ASSENZE_BRANCH')}
        if sha_corrente: payload["sha"] = sha_corrente
        
        logger.info(f"Tentativo di salvare '{file_path_in_repo}' su GitHub. SHA usato: {str(sha_corrente)[:7]}...")
        res = requests.put(target_api_url, headers=app_config.headers, json=payload, timeout=app_config.get('REQUEST_TIMEOUT'))
        res.raise_for_status()
        if res.status_code in [200, 201]:
            nuovo_sha = res.json()["content"]["sha"]
            logger.info(f"File '{file_path_in_repo}' salvato con successo. Nuovo SHA: {nuovo_sha[:7]}...")
            return True, nuovo_sha
        else:
            logger.warning(f"Salvataggio '{file_path_in_repo}' fallito con status {res.status_code}. Risposta: {res.text}")
            return False, sha_corrente # Restituisce lo SHA vecchio in caso di fallimento non HTTPError

    elif operazione == "carica" or operazione == "controlla":
        logger.info(f"Tentativo di {operazione} file '{file_path_in_repo}' da GitHub.")
        try:
            res = requests.get(target_api_url, headers=app_config.headers, timeout=app_config.get('REQUEST_TIMEOUT'))
            res.raise_for_status() # Solleva errore per 4xx/5xx tranne 404 gestito sotto
            
            contenuto_api = res.json()
            sha_file = contenuto_api["sha"]
            if operazione == "controlla":
                logger.info(f"File '{file_path_in_repo}' trovato. SHA: {sha_file[:7]}...")
                return True, sha_file # Esiste, restituisce True e SHA
            
            # Se operazione == "carica"
            dati_json_b64 = contenuto_api["content"]
            dati_decodificati = json.loads(base64.b64decode(dati_json_b64).decode('utf-8'))
            logger.info(f"File '{file_path_in_repo}' caricato. SHA: {sha_file[:7]}...")
            return dati_decodificati, sha_file
            
        except requests.exceptions.HTTPError as e_http_get:
            if e_http_get.response.status_code == 404:
                logger.info(f"File '{file_path_in_repo}' non trovato su GitHub (404).")
                return (None, None) if operazione == "carica" else (False, None) # Non esiste
            else: # Altri errori HTTP
                logger.error(f"Errore HTTP {e_http_get.response.status_code} durante {operazione} di '{file_path_in_repo}': {e_http_get.response.text}")
                raise # Rilancia l'eccezione perché il retry decorator la gestisca
        except json.JSONDecodeError as e_json_dec:
            logger.error(f"Errore decodifica JSON per '{file_path_in_repo}': {e_json_dec}. Contenuto grezzo: {base64.b64decode(contenuto_api.get('content','')).decode('utf-8', errors='ignore')[:200]}...")
            st.error(f"Il file '{file_path_in_repo}' su GitHub sembra corrotto.")
            return (None, None) if operazione == "carica" else (False, None) # Considera corrotto come non caricabile/non valido
    else:
        raise ValueError(f"Operazione '{operazione}' non supportata per opera_su_file_json_github.")

# --- VERIFICA CONNESSIONE GITHUB E INIT STATO --- (come prima, omesse per brevità)
if not SessionManager.get_safe('github_connection_checked'):
    conn_issues = verifica_connessione_github()
    if conn_issues: st.sidebar.error("⚠️ **Problemi Connessione GitHub:**"); [st.sidebar.error(f"  • {issue}") for issue in conn_issues]
    SessionManager.set_safe('github_connection_checked', True)
if not SessionManager.get_safe('elenco_medici_completo'):
    with st.spinner("Caricamento elenco medici..."): SessionManager.set_safe('elenco_medici_completo', inizializza_elenco_medici())

# --- FUNZIONI CALENDARIO --- (come prima, omesse per brevità)
@st.cache_data(ttl=3600, show_spinner="Generazione calendario...")
@monitor_performance("Generazione Calendario (Cached)")
def genera_calendario_cached(anno: int, mese: int, medici_tuple_sorted: tuple): # Accetta tupla ordinata
    try:
        medici_list = list(medici_tuple_sorted)
        logger.info(f"Cache miss o uso diretto: Genero calendario {mese}/{anno}, {len(medici_list)} medici")
        return genera_struttura_calendario(anno, mese, medici_list)
    except Exception as e: logger.error(f"Errore gen. calendario (cached): {e}", exc_info=True); st.error(f"Impossibile generare calendario: {e}"); return pd.DataFrame()
@monitor_performance("Creazione Struttura Calendario")
def genera_struttura_calendario(anno, mese, medici_selezionati):
    try:
        start_date = datetime(anno, mese, 1); _, ultimo_giorno = calendar.monthrange(anno, mese); end_date = datetime(anno, mese, ultimo_giorno)
        date_range_pd = pd.date_range(start=start_date, end=end_date, freq='D')
        try: festivita_anno = holidays.country_holidays("IT", years=anno)
        except Exception as e_hol: logger.warning(f"Festività {anno} non disponibili: {e_hol}"); st.sidebar.caption(f"⚠️ Festività {anno} non caricate."); festivita_anno = {}
        df_cols = {COL_DATA: date_range_pd, COL_GIORNO: [d.strftime("%A") for d in date_range_pd],
                   COL_FESTIVO: [d.date() in festivita_anno for d in date_range_pd], # Mantenuto per logica futura se serve
                   COL_NOME_FESTIVO: [festivita_anno.get(d.date(), "") for d in date_range_pd]} # Mantenuto per logica futura
        for medico in medici_selezionati: df_cols[medico] = "Presente" # Default per assenze
        df = pd.DataFrame(df_cols); logger.info(f"Calendario {mese}/{anno} creato: {len(df)} gg, {len(medici_selezionati)} medici."); return df
    except Exception as e: logger.error(f"Errore grave gen. struttura calendario: {e}", exc_info=True); st.error(f"Errore critico gen. calendario: {e}"); return pd.DataFrame()
@monitor_performance("Aggiornamento Calendario")
def aggiorna_calendario_se_necessario(anno, mese, medici_pianificati_lista):
    try:
        medici_set_frozen = frozenset(medici_pianificati_lista) # frozenset è hashable
        current_key = f"{anno}-{mese}-{hash(medici_set_frozen)}"
        if (SessionManager.get_safe('last_calendar_key') != current_key or SessionManager.get_safe('df_turni') is None):
            if medici_pianificati_lista: SessionManager.set_safe('df_turni', genera_calendario_cached(anno, mese, tuple(sorted(list(set(medici_pianificati_lista))))))
            else: SessionManager.set_safe('df_turni', pd.DataFrame())
            SessionManager.set_safe('last_calendar_key', current_key)
    except Exception as e: logger.error(f"Errore critico aggiornamento calendario: {e}", exc_info=True); st.error(f"Impossibile aggiornare calendario: {e}"); SessionManager.set_safe('df_turni', pd.DataFrame())

# --- UI SIDEBAR (Gestione Medici, Selezione Periodo) --- (come prima, omesse per brevità)
st.sidebar.title("🗓️ Gestione Turni")
st.sidebar.markdown("App per la pianificazione dei turni medici.")
st.sidebar.divider(); st.sidebar.header("👨‍⚕️ Medici")
with st.sidebar.form("form_aggiungi_medico", clear_on_submit=True):
    nuovo_medico_input = st.text_input("➕ Nome nuovo medico (es. Rossi Mario)").strip()
    submitted_add = st.form_submit_button("Aggiungi Medico", type="primary")
if submitted_add and nuovo_medico_input:
    valido, msg_o_nome_norm = valida_nome_medico_v2(nuovo_medico_input, SessionManager.get_safe('elenco_medici_completo', []))
    if not valido: st.sidebar.error(msg_o_nome_norm)
    else:
        elenco_aggiornato = SessionManager.get_safe('elenco_medici_completo', []) + [msg_o_nome_norm]; elenco_aggiornato.sort()
        with st.spinner("Salvataggio medico..."):
            try:
                if salva_medici_su_github(elenco_aggiornato, SessionManager.get_safe("sha_medici")):
                    SessionManager.set_safe('elenco_medici_completo', elenco_aggiornato)
                    st.toast(f"Medico '{msg_o_nome_norm}' aggiunto!", icon="✅"); logger.info(f"Medico aggiunto GitHub: {msg_o_nome_norm}"); st.rerun()
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
                    SessionManager.clear_calendar_related_state()
                    current_medici_pianificati = SessionManager.get_safe('medici_pianificati', [])
                    if medico_da_rimuovere in current_medici_pianificati: current_medici_pianificati.remove(medico_da_rimuovere); SessionManager.set_safe('medici_pianificati', current_medici_pianificati)
                    SessionManager.set_safe("medico_da_rimuovere_selection", options_rimuovi[0]); st.rerun()
            except Exception as e_remove: logger.error(f"Eccezione rimozione medico: {e_remove}", exc_info=True); st.sidebar.error(f"❌ Errore critico rimozione: {e_remove}")
else: st.sidebar.caption("Nessun medico nell'elenco.")
st.sidebar.divider(); st.sidebar.header("🎯 Pianificazione")
default_medici_pianif = SessionManager.get_safe('medici_pianificati', [])
valid_default_medici = [m for m in default_medici_pianif if m in elenco_medici_corrente]
if not valid_default_medici and elenco_medici_corrente: valid_default_medici = elenco_medici_corrente[:]
medici_pianificati = st.sidebar.multiselect("👨‍⚕️ Medici per input assenze:", options=sorted(list(set(elenco_medici_corrente))), default=valid_default_medici, key="multi_medici_pianif", help="Seleziona medici per cui inserire le assenze.")
st.sidebar.header("🗓️ Periodo Assenze")
oggi = datetime.today(); anni_disponibili = list(range(oggi.year - 1, oggi.year + 3)) # Range anni più contenuto
idx_mese_default = SessionManager.get_safe('selected_mese_index', oggi.month - 1); idx_mese_default = oggi.month - 1 if not 0 <= idx_mese_default < 12 else idx_mese_default
# Trova l'indice di oggi.year o il più vicino se non presente
try: idx_anno_default = anni_disponibili.index(SessionManager.get_safe('selected_anno_val', oggi.year))
except ValueError: idx_anno_default = anni_disponibili.index(oggi.year) if oggi.year in anni_disponibili else 0
idx_anno_default = anni_disponibili.index(oggi.year) if not 0 <= idx_anno_default < len(anni_disponibili) else idx_anno_default

col1_sb, col2_sb = st.sidebar.columns(2); lista_mesi = list(range(1, 13))
selected_mese = col1_sb.selectbox("Mese:", lista_mesi, index=idx_mese_default, format_func=lambda x: calendar.month_name[x], key="sel_mese")
selected_anno = col2_sb.selectbox("Anno:", anni_disponibili, index=idx_anno_default, key="sel_anno")

# --- LOGICA DI AGGIORNAMENTO PRINCIPALE (PERIODO E MEDICI) ---
if SessionManager.get_safe('medici_pianificati', []) != medici_pianificati:
    SessionManager.set_safe('medici_pianificati', medici_pianificati)
    aggiorna_calendario_se_necessario(selected_anno, selected_mese, medici_pianificati) 
if (SessionManager.get_safe('selected_mese_val') != selected_mese or SessionManager.get_safe('selected_anno_val') != selected_anno):
    SessionManager.set_safe('selected_mese_val',selected_mese); SessionManager.set_safe('selected_anno_val',selected_anno)
    SessionManager.set_safe('selected_mese_index', lista_mesi.index(selected_mese)); SessionManager.set_safe('selected_anno_index', anni_disponibili.index(selected_anno))
    aggiorna_calendario_se_necessario(selected_anno, selected_mese, medici_pianificati)
elif SessionManager.get_safe('df_turni') is None:
     aggiorna_calendario_se_necessario(selected_anno, selected_mese, medici_pianificati)
nome_mese_corrente = calendar.month_name[selected_mese]

# --- AREA PRINCIPALE ---
st.title(f"📝 Input Assenze Medici")
st.markdown(f"### Periodo: {nome_mese_corrente} {selected_anno}")
df_turni_corrente = SessionManager.get_safe('df_turni')

if not medici_pianificati: st.info("👈 **Nessun medico selezionato.** Scegli dalla sidebar.")
elif df_turni_corrente is None or df_turni_corrente.empty:
    st.warning("📅 Il modulo per l'input delle assenze è vuoto. Verifica selezioni o ricarica."); logger.warning(f"df_turni vuoto/None per input. Medici: {len(medici_pianificati)}.")
else:
    st.markdown("#### 🗓️ **Inserisci le assenze per ciascun medico selezionato:**")
    cols_per_editor = [COL_DATA, COL_GIORNO] + medici_pianificati # Giorno per contesto
    column_config_editor = {
        COL_DATA: st.column_config.DateColumn("Data", format="DD/MM/YYYY", disabled=True, width="small"),
        COL_GIORNO: st.column_config.TextColumn("Giorno", disabled=True, width="small")}
    for medico in medici_pianificati:
        nome_cognome = medico.split(); nome_display = nome_cognome[-1].capitalize() if len(nome_cognome) > 1 else medico.capitalize()
        column_config_editor[medico] = st.column_config.SelectboxColumn(f"Dr. {nome_display}", help=f"Assenza per {medico}", options=TIPI_ASSENZA, required=True, width="medium")
    
    df_editor_input_valid = all(col_edit in df_turni_corrente.columns for col_edit in cols_per_editor)
    if not df_editor_input_valid:
        missing_cols = [c for c in cols_per_editor if c not in df_turni_corrente.columns]
        logger.error(f"Colonne mancanti in df_turni per editor: {missing_cols}. Disponibili: {df_turni_corrente.columns.tolist()}")
        st.error(f"Errore interno: colonne {missing_cols} non trovate per editor.")
    else:
        df_editor_input = df_turni_corrente[cols_per_editor].copy()
        editor_key = f"data_editor_assenze_{SessionManager.get_safe('last_calendar_key', 'default_key')}"
        try:
            edited_df_assenze = st.data_editor(df_editor_input, column_config=column_config_editor, use_container_width=True,
                                             hide_index=True, num_rows="fixed", key=editor_key, 
                                             height=(len(df_editor_input) + 1) * ROW_HEIGHT_PX + TABLE_PADDING_PX)
            modifiche_editor = False
            for medico_col in medici_pianificati:
                if df_turni_corrente[medico_col].dtype == edited_df_assenze[medico_col].dtype and not df_turni_corrente[medico_col].equals(edited_df_assenze[medico_col]):
                    # Aggiorna direttamente il DataFrame in session_state
                    SessionManager.get_safe('df_turni')[medico_col] = edited_df_assenze[medico_col].copy(); modifiche_editor = True
            if modifiche_editor: st.toast("Modifiche alle assenze registrate localmente.", icon="📝"); logger.info("Assenze modificate e aggiornate in session_state.")
        except Exception as e_data_editor: logger.error(f"Errore st.data_editor: {e_data_editor}", exc_info=True); st.error("⚠️ Errore editor assenze. Ricarica.")
    
    st.divider()
    st.markdown("#### 💾 **Salva Assenze su GitHub**")
    
    nome_file_assenze_json = f"{ASSENZE_FILE_PREFIX}_{selected_anno}_{selected_mese:02}.json"
    path_completo_file_assenze = nome_file_assenze_json # Se vuoi metterlo in una sottocartella, es. "dati_assenze/" + nome_file_assenze_json

    # Bottone per salvare il JSON su GitHub
    if st.button(f"📤 Salva {nome_file_assenze_json} su GitHub", key="btn_salva_json_github", type="primary", help="Salva le assenze correnti come file JSON nel repository GitHub."):
        if df_turni_corrente is not None and not df_turni_corrente.empty:
            # Prepara i dati per il JSON: un dizionario per medico con le sue assenze
            dati_json_da_salvare = {"anno": selected_anno, "mese": selected_mese, "medici": {}}
            df_per_json = df_turni_corrente.copy()
            df_per_json[COL_DATA] = df_per_json[COL_DATA].dt.strftime('%Y-%m-%d') # Formato data standard per JSON

            for medico in medici_pianificati:
                # Salva solo i giorni in cui il medico NON è 'Presente' per compattezza
                assenze_medico = df_per_json[df_per_json[medico] != "Presente"][[COL_DATA, medico]].rename(columns={medico: "tipo_assenza"}).to_dict(orient='records')
                if assenze_medico: # Solo se ci sono effettive assenze
                    dati_json_da_salvare["medici"][medico] = assenze_medico
            
            if not dati_json_da_salvare["medici"]: # Se nessun medico ha assenze registrate
                 st.info("Nessuna assenza registrata (diversa da 'Presente'). Verrà salvato un file con struttura base.", icon="ℹ️")
                 # Puoi decidere se salvare comunque un file vuoto o meno. Qui lo salvo.
            
            with st.spinner(f"Controllo e salvataggio di '{path_completo_file_assenze}' su GitHub..."):
                try:
                    # 1. Controlla se il file esiste già per ottenere lo SHA
                    sha_file_assenze_attuale = SessionManager.get_safe('sha_assenze', {}).get(path_completo_file_assenze)
                    if not sha_file_assenze_attuale: # Se non in sessione, prova a caricarlo da GitHub
                        _, sha_file_assenze_attuale = opera_su_file_json_github(path_completo_file_assenze, operazione="controlla")
                    
                    # 2. Salva il file (crea o aggiorna)
                    successo_salvataggio, nuovo_sha_assenze = opera_su_file_json_github(
                        file_path_in_repo=path_completo_file_assenze,
                        dati_da_salvare=dati_json_da_salvare,
                        sha_corrente=sha_file_assenze_attuale,
                        operazione="salva"
                    )
                    if successo_salvataggio:
                        # Aggiorna lo SHA del file delle assenze in session_state
                        sha_assenze_dict = SessionManager.get_safe('sha_assenze', {})
                        sha_assenze_dict[path_completo_file_assenze] = nuovo_sha_assenze
                        SessionManager.set_safe('sha_assenze', sha_assenze_dict)
                        st.success(f"🎉 File '{path_completo_file_assenze}' salvato con successo su GitHub!")
                        logger.info(f"File assenze '{path_completo_file_assenze}' salvato su GitHub. Nuovo SHA: {nuovo_sha_assenze[:7]}...")
                    else:
                        st.error(f"❌ Impossibile salvare il file '{path_completo_file_assenze}' su GitHub. Controlla i log.")
                except Exception as e_salva_json:
                    logger.error(f"Errore critico durante il salvataggio del JSON delle assenze: {e_salva_json}", exc_info=True)
                    st.error(f"❌ Errore imprevisto: {e_salva_json}")
        else:
            st.warning("Nessun dato di assenze da salvare (calendario vuoto).")

st.sidebar.divider()
st.sidebar.markdown(f"""<div style="font-size: 0.8em; text-align: center; color: grey;">
    Input Assenze Medici v1.2<br>
    {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</div>""", unsafe_allow_html=True)
logger.info("--- Rendering pagina completato (Input Assenze) ---")
