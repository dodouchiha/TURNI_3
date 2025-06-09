import streamlit as st
import pandas as pd
import calendar
from datetime import datetime
import holidays
from io import BytesIO
import requests
import json
import base64

# --- CONFIGURAZIONE INIZIALE ---
st.set_page_config(page_title="Gestione Turni Medici", layout="wide")

# --- COSTANTI ---
COL_DATA = "Data"
COL_GIORNO = "Giorno"
COL_FESTIVO = "Festivo"
COL_NOME_FESTIVO = "Nome Festivo"
COL_AMBULATORIO = "Ambulatorio"
TIPI_ASSENZA = ["Presente", "Ferie", "Malattia", "Congresso", "Lezione", "Altro"] # "Presente" è più chiaro di "Nessuna"

# --- CONFIGURAZIONE GITHUB ---
GITHUB_USER = "dodouchiha"
REPO_NAME = "turni_3"
FILE_PATH = "medici.json"
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN") # Usare .get() per evitare errori se la secret non è impostata

if not GITHUB_TOKEN:
    st.error("Token GitHub non configurato nelle secrets! L'applicazione non potrà salvare i dati.")
    st.stop()

API_URL = f"https://api.github.com/repos/{GITHUB_USER}/{REPO_NAME}/contents/{FILE_PATH}"
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

# --- FUNZIONI GITHUB ---
def carica_medici():
    """Carica l'elenco dei medici da GitHub."""
    try:
        res = requests.get(API_URL, headers=HEADERS)
        res.raise_for_status() # Solleva un errore per status codes 4xx/5xx
        contenuto = res.json()
        file_sha = contenuto["sha"]
        elenco_json = base64.b64decode(contenuto["content"]).decode('utf-8')
        elenco = json.loads(elenco_json)
        st.session_state.sha_medici = file_sha # Salva lo SHA in session_state
        return elenco
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404: # File non trovato
            st.warning(f"File '{FILE_PATH}' non trovato su GitHub. Verrà creato al primo salvataggio.")
            st.session_state.sha_medici = None
            return []
        else:
            st.error(f"Errore GitHub (carica): {e.response.status_code} - {e.response.text}")
            return [] # Ritorna lista vuota in caso di altri errori
    except Exception as e:
        st.error(f"Errore imprevisto durante il caricamento dei medici: {e}")
        return []

def salva_medici(lista_medici):
    """Salva l'elenco dei medici su GitHub."""
    sha = st.session_state.get("sha_medici")
    blob = json.dumps(lista_medici, indent=2).encode('utf-8')
    encoded_content = base64.b64encode(blob).decode('utf-8')

    data = {
        "message": f"Aggiornamento elenco medici - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "content": encoded_content,
        "branch": "main"
    }
    if sha: # Se lo SHA esiste, stiamo aggiornando un file esistente
        data["sha"] = sha
    
    try:
        res = requests.put(API_URL, headers=HEADERS, json=data)
        res.raise_for_status()
        
        # Aggiorna lo SHA dopo il salvataggio
        if res.status_code == 200 or res.status_code == 201: # 200 OK (update), 201 Created (new file)
            st.session_state.sha_medici = res.json()["content"]["sha"]
            return True
        return False
    except requests.exceptions.HTTPError as e:
        st.error(f"Errore GitHub (salva): {e.response.status_code} - {e.response.text}")
        try:
            st.error(f"Dettagli errore: {e.response.json()}")
        except ValueError: # In caso il corpo della risposta non sia JSON
            pass
        return False
    except Exception as e:
        st.error(f"Errore imprevisto durante il salvataggio dei medici: {e}")
        return False

# --- INIZIALIZZAZIONE STATO ---
if 'elenco_medici_completo' not in st.session_state:
    st.session_state.elenco_medici_completo = carica_medici()
if 'sha_medici' not in st.session_state: # Potrebbe essere già stato impostato da carica_medici
    st.session_state.sha_medici = None # Inizializza se non presente

# --- GESTIONE MEDICI - Sidebar ---
st.sidebar.header("👨‍⚕️ Gestione Medici")

with st.sidebar.form("form_aggiungi_medico"):
    nuovo_medico = st.text_input("➕ Aggiungi nuovo medico").strip()
    submitted_add = st.form_submit_button("Aggiungi Medico")

if submitted_add and nuovo_medico:
    if nuovo_medico not in st.session_state.elenco_medici_completo:
        st.session_state.elenco_medici_completo.append(nuovo_medico)
        st.session_state.elenco_medici_completo.sort() # Opzionale: mantiene la lista ordinata
        if salva_medici(st.session_state.elenco_medici_completo):
            st.sidebar.success(f"✅ Medico '{nuovo_medico}' aggiunto e salvato.")
            st.experimental_rerun() # Ricarica per aggiornare le selectbox, ecc.
        else:
            st.sidebar.error("❌ Errore nel salvataggio del medico su GitHub.")
            # Ripristina lo stato precedente in caso di fallimento del salvataggio
            st.session_state.elenco_medici_completo.remove(nuovo_medico)
    else:
        st.sidebar.warning(f"'{nuovo_medico}' è già presente nell'elenco.")

if st.session_state.elenco_medici_completo:
    medico_da_rimuovere = st.sidebar.selectbox(
        "🗑️ Rimuovi medico",
        options=[""] + st.session_state.elenco_medici_completo,
        index=0
    )
    if st.sidebar.button("Conferma Rimozione") and medico_da_rimuovere:
        medici_temp = st.session_state.elenco_medici_completo.copy()
        medici_temp.remove(medico_da_rimuovere)
        if salva_medici(medici_temp):
            st.session_state.elenco_medici_completo = medici_temp
            st.sidebar.success(f"✅ Medico '{medico_da_rimuovere}' rimosso e salvato.")
            # Se il medico rimosso era selezionato per la pianificazione, deselezionalo
            if 'medici_pianificati' in st.session_state and medico_da_rimuovere in st.session_state.medici_pianificati:
                st.session_state.medici_pianificati.remove(medico_da_rimuovere)
            if 'df_turni' in st.session_state: # Forza la rigenerazione del df
                del st.session_state.df_turni
            st.experimental_rerun()
        else:
            st.sidebar.error("❌ Errore nella rimozione del medico su GitHub.")

st.sidebar.markdown("---")
medici_da_pianificare_default = st.session_state.get('medici_pianificati', st.session_state.elenco_medici_completo)
medici_pianificati = st.sidebar.multiselect(
    "✅ Seleziona medici da pianificare",
    options=st.session_state.elenco_medici_completo,
    default=medici_da_pianificare_default
)
st.session_state.medici_pianificati = medici_pianificati


# --- SELEZIONE MESE E ANNO ---
st.sidebar.markdown("---")
st.sidebar.header("🗓️ Selezione Periodo")
oggi = datetime.today()
col1_sidebar, col2_sidebar = st.sidebar.columns(2)
selected_mese = col1_sidebar.selectbox(
    "Mese",
    list(range(1, 13)),
    index=oggi.month - 1,
    format_func=lambda x: calendar.month_name[x],
    key="selected_mese"
)
selected_anno = col2_sidebar.selectbox(
    "Anno",
    list(range(oggi.year - 2, oggi.year + 5)), # Range più ampio
    index=2,
    key="selected_anno"
)
nome_mese_corrente = calendar.month_name[selected_mese]

# --- LOGICA PER GENERARE/AGGIORNARE IL DATAFRAME DEI TURNI ---
def genera_struttura_calendario(anno, mese, medici_selezionati):
    """Genera il DataFrame base del calendario con le assenze."""
    _, ultimo_giorno = calendar.monthrange(anno, mese)
    date_del_mese = pd.date_range(start=f"{anno}-{mese:02d}-01", end=f"{anno}-{mese:02d}-{ultimo_giorno}")
    
    try:
        festivita_anno = holidays.country_holidays("IT", years=anno)
    except KeyError: # Può capitare se l'anno non è supportato dalla libreria
        st.warning(f"Festività per l'anno {anno} non disponibili. Procedo senza.")
        festivita_anno = {}

    def is_giorno_ambulatorio(data_controllo):
        return data_controllo.weekday() in [0, 2, 4] and data_controllo.date() not in festivita_anno

    df = pd.DataFrame({
        COL_DATA: date_del_mese,
        COL_GIORNO: date_del_mese.strftime("%A"),
        COL_FESTIVO: pd.Series(date_del_mese).dt.date.isin(festivita_anno.keys()),
        COL_NOME_FESTIVO: [festivita_anno.get(d.date(), "") for d in date_del_mese],
        COL_AMBULATORIO: ["Ambulatorio" if is_giorno_ambulatorio(d) else "" for d in date_del_mese]
    })

    for medico in medici_selezionati:
        df[medico] = "Presente" # Default per le assenze

    return df

# Chiave per identificare univocamente la configurazione corrente del calendario
calendar_config_key = f"{selected_anno}-{selected_mese}-{'_'.join(sorted(medici_pianificati))}"

# Se la configurazione è cambiata o il df non esiste, lo rigenera
if 'current_calendar_config_key' not in st.session_state or \
   st.session_state.current_calendar_config_key != calendar_config_key or \
   'df_turni' not in st.session_state:
    
    st.session_state.df_turni = genera_struttura_calendario(selected_anno, selected_mese, medici_pianificati)
    st.session_state.current_calendar_config_key = calendar_config_key


# --- VISUALIZZAZIONE E MODIFICA CALENDARIO ---
st.header(f"🗓️ Pianificazione Turni per {nome_mese_corrente} {selected_anno}")

if not medici_pianificati:
    st.info("Nessun medico selezionato per la pianificazione. Selezionane almeno uno dalla sidebar.")
elif 'df_turni' in st.session_state and not st.session_state.df_turni.empty:
    
    # Configurazione per st.data_editor
    column_config = {
        COL_DATA: st.column_config.DateColumn("Data", format="DD/MM/YYYY", disabled=True),
        COL_GIORNO: st.column_config.TextColumn("Giorno", disabled=True),
        COL_FESTIVO: st.column_config.CheckboxColumn("Festivo?", disabled=True),
        COL_NOME_FESTIVO: st.column_config.TextColumn("Festività", disabled=True),
        COL_AMBULATORIO: st.column_config.TextColumn("Ambulatorio", disabled=True),
    }
    for medico in medici_pianificati:
        column_config[medico] = st.column_config.SelectboxColumn(
            f"Assenza {medico}",
            options=TIPI_ASSENZA,
            required=True # Ogni medico deve avere uno stato
        )
    
    st.markdown("#### Inserisci/Modifica Assenze:")
    edited_df = st.data_editor(
        st.session_state.df_turni,
        column_config=column_config,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic", # Teoricamente non necessario se il numero di giorni è fisso
        key=f"data_editor_{calendar_config_key}" # Chiave dinamica per forzare il reset se cambia la struttura
    )

    # Aggiorna il DataFrame in session_state se ci sono state modifiche
    if not edited_df.equals(st.session_state.df_turni):
        st.session_state.df_turni = edited_df
        # st.success("Modifiche alle assenze applicate.") # Potrebbe essere troppo verboso
        # Non serve un rerun qui, st.data_editor gestisce l'aggiornamento dell'UI

    # --- STILE DATAFRAME (per la visualizzazione finale, se diversa dall'editor) ---
    def evidenzia_righe(row):
        style = ['border: 1px solid #e0e0e0'] * len(row) # Stile base per tutte le celle
        is_weekend = row[COL_DATA].weekday() in [5, 6] # Sabato o Domenica

        for i, col_name in enumerate(row.index):
            cell_style = []
            # Colora weekend e festivi
            if row[COL_FESTIVO] or is_weekend:
                cell_style.append("background-color: #f0f0f0") # Grigio chiaro per weekend/festivi

            # Evidenzia giorni di Ambulatorio
            if col_name == COL_AMBULATORIO and row[COL_AMBULATORIO] == "Ambulatorio":
                cell_style.append("background-color: #e6f7ff; font-weight: bold;") # Azzurrino per Ambulatorio

            # Evidenzia assenze dei medici
            if col_name in medici_pianificati and row[col_name] != "Presente":
                cell_style.append("background-color: #ffecb3; font-style: italic;") # Giallo chiaro per assenze
            
            if cell_style: # Applica solo se c'è qualche stile specifico
                 # Sovrascrivi lo stile di base per questa cella se necessario
                current_bg = next((s.split(': ')[1] for s in cell_style if 'background-color' in s), None)
                if current_bg and "background-color: #f0f0f0" in cell_style and len(cell_style) > 1:
                    # Se è un weekend/festivo E ha un altro colore (es. assenza), usa l'altro colore
                    # Questo evita che l'assenza su un festivo sia solo grigia
                    final_bg_styles = [s for s in cell_style if 'background-color' not in s or s == f"background-color: {current_bg}"]
                    style[i] = '; '.join(final_bg_styles)
                else:
                    style[i] = '; '.join(cell_style)
        return style
    
    # Per la visualizzazione, potresti voler mostrare il df formattato
    # st.subheader(f"📅 Riepilogo Calendario {nome_mese_corrente} {selected_anno}")
    # st.dataframe(st.session_state.df_turni.style.apply(evidenzia_righe, axis=1), use_container_width=True)
    # Nota: st.data_editor non supporta lo styling diretto come .style.apply.
    # Lo styling qui sopra sarebbe per un st.dataframe() separato.
    # Per semplicità, ci affidiamo alla modifica diretta con st.data_editor.

    # --- ESPORTAZIONE ---
    st.markdown("---")
    def to_excel(df_to_export):
        output_buffer = BytesIO()
        with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
            df_to_export.to_excel(writer, index=False, sheet_name=f"Turni_{selected_anno}_{selected_mese:02}")
        output_buffer.seek(0)
        return output_buffer

    excel_bytes = to_excel(st.session_state.df_turni.copy()) # Usa una copia per evitare problemi
    st.download_button(
        label="📥 Scarica Calendario Attuale in Excel",
        data=excel_bytes,
        file_name=f"turni_{selected_anno}_{selected_mese:02d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

else:
    if medici_pianificati: # Se ci sono medici ma il df è vuoto (improbabile con la logica attuale)
        st.warning("Il DataFrame dei turni è vuoto. Prova a ricaricare o cambiare selezione.")

st.sidebar.markdown("---")
st.sidebar.caption(f"Versione 0.2 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
