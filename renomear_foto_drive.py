import os
import pickle
import sys
import time
from datetime import datetime, timezone

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Habilita cores ANSI nativas no terminal do Windows
if os.name == "nt":
    os.system("")

# ==========================================
# CONFIGURAÇÕES
# ==========================================
SCOPES = ["https://www.googleapis.com/auth/drive"]
BATCH_SIZE = 50  # Máximo seguro por lote na API do Google Drive
EXTS_FOTO = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".webp"}
EXTS_VIDEO_LIVE = {".mp4", ".mov"}
MAX_DURACAO_LIVE_MS = 5000  # Vídeos de Live Photo duram ~1.5s a 3s


class Cor:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    VERDE = "\033[92m"
    CIANO = "\033[96m"
    AMARELO = "\033[93m"
    VERMELHO = "\033[91m"
    MAGENTA = "\033[95m"
    CINZA = "\033[90m"


def resolver_caminho(nome: str) -> str:
    diretoria_script = os.path.dirname(os.path.abspath(__file__))
    locais = [
        os.path.join(diretoria_script, nome),
        os.path.join(diretoria_script, "Auxiliar_Codigos", "renomeador-fotos", nome),
        os.path.join(diretoria_script, "..", "Auxiliar_Codigos", "renomeador-fotos", nome),
        os.path.join(os.getcwd(), nome),
    ]
    for caminho in locais:
        if os.path.exists(caminho):
            return os.path.abspath(caminho)
    return os.path.join(diretoria_script, nome)


def autenticar():
    caminho_token = resolver_caminho("token.pickle")
    creds = None

    if os.path.exists(caminho_token):
        try:
            with open(caminho_token, "rb") as token:
                creds = pickle.load(token)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                print(f"{Cor.AMARELO}⚠ Token expirado/revogado. Abrindo navegador para login...{Cor.RESET}")
                creds = None

        if not creds or not creds.valid:
            caminho_cred = resolver_caminho("credentials.json")
            if not os.path.exists(caminho_cred):
                print(f"{Cor.VERMELHO}❌ Não foi possível encontrar 'credentials.json'!{Cor.RESET}")
                sys.exit(1)
            print(f"{Cor.CIANO}🌐 Abrindo navegador para autorizar acesso ao Google Drive...{Cor.RESET}")
            flow = InstalledAppFlow.from_client_secrets_file(caminho_cred, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(caminho_token, "wb") as token:
            pickle.dump(creds, token)

    return build("drive", "v3", credentials=creds)


def escapar_query(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("'", "\\'")


def procurar_pasta(service, nome_pasta: str, parent_id: str = "root") -> str | None:
    nome_seguro = escapar_query(nome_pasta)
    query = (
        f"mimeType = 'application/vnd.google-apps.folder' and "
        f"name = '{nome_seguro}' and "
        f"'{parent_id}' in parents and "
        f"trashed = false"
    )
    res = service.files().list(q=query, fields="files(id, name)").execute()
    pastas = res.get("files", [])
    return pastas[0]["id"] if pastas else None


def listar_conteudo_pasta(service, folder_id: str, recursivo: bool = False) -> list[dict]:
    arquivos = []
    subpastas = []
    page_token = None

    query = f"'{folder_id}' in parents and trashed = false"

    while True:
        res = service.files().list(
            q=query,
            pageSize=1000,
            pageToken=page_token,
            fields="nextPageToken, files(id, name, mimeType, createdTime, imageMediaMetadata, videoMediaMetadata)",
        ).execute()

        for item in res.get("files", []):
            if item.get("mimeType") == "application/vnd.google-apps.folder":
                subpastas.append(item)
            else:
                item["_folder_id"] = folder_id
                arquivos.append(item)

        page_token = res.get("nextPageToken")
        if not page_token:
            break

    if recursivo:
        for sub in subpastas:
            arquivos.extend(listar_conteudo_pasta(service, sub["id"], recursivo=True))

    return arquivos


def parse_data(str_data: str | None, is_utc: bool = False) -> datetime | None:
    if not str_data:
        return None

    termina_z = str_data.endswith("Z")
    limpa = str_data.replace("Z", "").split(".")[0]

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(limpa, fmt)
            if is_utc or termina_z:
                dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
            return dt
        except ValueError:
            pass
    return None


def detectar_live_photos(arquivos: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separa vídeos curtos de Live Photo que possuem foto irmã na mesma pasta."""
    por_pasta: dict[str, list[dict]] = {}
    for item in arquivos:
        por_pasta.setdefault(item["_folder_id"], []).append(item)

    arquivos_limpos = []
    live_videos_lixeira = []

    for fid, itens in por_pasta.items():
        bases_fotos = set()

        for it in itens:
            base, ext = os.path.splitext(it["name"])
            if ext.lower() in EXTS_FOTO:
                bases_fotos.add(base.lower())

        for it in itens:
            base, ext = os.path.splitext(it["name"])
            ext_low = ext.lower()

            if ext_low in EXTS_VIDEO_LIVE:
                duracao_ms = int(it.get("videoMediaMetadata", {}).get("durationMillis") or 0)
                mesmo_nome = base.lower() in bases_fotos
                eh_curto = 0 < duracao_ms <= MAX_DURACAO_LIVE_MS

                if mesmo_nome and eh_curto:
                    live_videos_lixeira.append(it)
                    continue

            arquivos_limpos.append(it)

    return arquivos_limpos, live_videos_lixeira


def planejar_renomeacoes(arquivos: list[dict]) -> tuple[list[dict], list[str], int]:
    sem_data = []
    por_pasta: dict[str, list[tuple[datetime, dict]]] = {}

    for item in arquivos:
        meta_time = item.get("imageMediaMetadata", {}).get("time")
        created_time = item.get("createdTime")

        dt = parse_data(meta_time, is_utc=False) or parse_data(created_time, is_utc=True)
        if not dt:
            sem_data.append(item["name"])
            continue

        fid = item["_folder_id"]
        por_pasta.setdefault(fid, []).append((dt, item))

    plano = []
    ja_corretos = 0

    for fid, lista_itens in por_pasta.items():
        lista_itens.sort(key=lambda x: (x[0], x[1]["id"]))
        nomes_usados = set()

        for dt, item in lista_itens:
            nome_antigo = item["name"]
            _, ext = os.path.splitext(nome_antigo)
            ext = ext.lower() if ext else ".jpg"

            base_nome = dt.strftime("%Y-%m-%d_%Hh%M")
            novo_nome = f"{base_nome}{ext}"

            contador = 1
            while novo_nome in nomes_usados:
                novo_nome = f"{base_nome}_{contador}{ext}"
                contador += 1
            nomes_usados.add(novo_nome)

            if nome_antigo == novo_nome:
                ja_corretos += 1
                continue

            plano.append({
                "id": item["id"],
                "antigo": nome_antigo,
                "novo": novo_nome,
            })

    return plano, sem_data, ja_corretos


def imprimir_barra(done: int, total: int):
    largura = 30
    preenchido = int(largura * done / total)
    barra = f"{Cor.CIANO}{'█' * preenchido}{Cor.CINZA}{'░' * (largura - preenchido)}{Cor.RESET}"
    pct = int((done / total) * 100)
    sys.stdout.write(f"\r  Processando: [{barra}] {pct:3d}% ({done}/{total})")
    sys.stdout.flush()


def executar_lotes(service, plano_renomear: list[dict], plano_lixeira: list[dict]) -> tuple[int, int, list[str]]:
    renomeados_ok = 0
    lixeira_ok = 0
    erros = []

    operacoes = []
    for item in plano_lixeira:
        operacoes.append(("trash", item["id"], item["name"], {"trashed": True}))
    for item in plano_renomear:
        operacoes.append(("rename", item["id"], item["antigo"], {"name": item["novo"]}))

    total = len(operacoes)
    processados = 0

    for i in range(0, total, BATCH_SIZE):
        lote = operacoes[i : i + BATCH_SIZE]
        mapa_lote = {op[1]: op for op in lote}

        def callback(request_id, response, exception):
            nonlocal renomeados_ok, lixeira_ok, processados
            processados += 1
            op = mapa_lote.get(request_id)
            if exception is not None:
                nome = op[2] if op else request_id
                erros.append(f"{nome} -> {exception}")
            else:
                if op and op[0] == "trash":
                    lixeira_ok += 1
                else:
                    renomeados_ok += 1

        batch = service.new_batch_http_request(callback=callback)
        for tipo, file_id, _, body_payload in lote:
            batch.add(
                service.files().update(fileId=file_id, body=body_payload),
                request_id=file_id,
            )

        batch.execute()
        imprimir_barra(processados, total)

    print()
    return renomeados_ok, lixeira_ok, erros


def main():
    print(f"\n{Cor.BOLD}{Cor.CIANO}╔══════════════════════════════════════════════════════╗{Cor.RESET}")
    print(f"{Cor.BOLD}{Cor.CIANO}║      📸 RENOMEADOR DE FOTOS DO DRIVE  v2.2           ║{Cor.RESET}")
    print(f"{Cor.BOLD}{Cor.CIANO}╚══════════════════════════════════════════════════════╝{Cor.RESET}\n")

    # 1) Pede Ano e Mês imediatamente ao abrir o script
    ano = input(f"  {Cor.BOLD}Ano:{Cor.RESET} ").strip()
    mes = input(f"  {Cor.BOLD}Mês{Cor.RESET} (Enter para pasta do ano): ").strip()

    recursivo = False
    if not mes:
        resp_rec = input(f"  {Cor.BOLD}Incluir subpastas dentro de {ano}?{Cor.RESET} [s/N]: ").strip().lower()
        recursivo = resp_rec == "s"

    # 2) Conecta no Google Drive
    print(f"\n  {Cor.CINZA}🔐 Conectando ao Google Drive...{Cor.RESET}")
    service = autenticar()

    id_fotos = procurar_pasta(service, "Fotos", "root")
    if not id_fotos:
        print(f"\n{Cor.VERMELHO}❌ Pasta 'Fotos' não encontrada na raiz do Drive.{Cor.RESET}")
        return

    id_ano = procurar_pasta(service, ano, id_fotos)
    if not id_ano:
        print(f"\n{Cor.VERMELHO}❌ Pasta '{ano}' não encontrada dentro de 'Fotos'.{Cor.RESET}")
        return

    if mes:
        id_alvo = procurar_pasta(service, mes, id_ano)
        if not id_alvo:
            print(f"\n{Cor.VERMELHO}❌ Pasta '{mes}' não encontrada em 'Fotos > {ano}'.{Cor.RESET}")
            return
        caminho_label = f"Fotos > {ano} > {mes}"
    else:
        id_alvo = id_ano
        caminho_label = f"Fotos > {ano}" + (" (com subpastas)" if recursivo else "")

    print(f"  {Cor.CIANO}🔍 Lendo arquivos em: {caminho_label}...{Cor.RESET}")
    arquivos = listar_conteudo_pasta(service, id_alvo, recursivo=recursivo)

    if not arquivos:
        print(f"  {Cor.AMARELO}Nenhum arquivo encontrado.{Cor.RESET}\n")
        return

    # 3) Detecta vídeos de Live Photo duplicados
    arquivos_para_processar, live_videos = detectar_live_photos(arquivos)

    # 4) Planeja renomeações do restante
    plano, sem_data, ja_corretos = planejar_renomeacoes(arquivos_para_processar)

    print(f"\n  {Cor.BOLD}Resumo da Análise:{Cor.RESET}")
    print(f"    {Cor.CINZA}● Total encontrados:       {len(arquivos)}{Cor.RESET}")
    print(f"    {Cor.VERDE}● Já estão corretos:       {ja_corretos}{Cor.RESET}")
    print(f"    {Cor.CIANO}● Para renomear:           {len(plano)}{Cor.RESET}")
    if live_videos:
        print(f"    {Cor.MAGENTA}● Vídeos Live Photo (dup): {len(live_videos)} (irão p/ Lixeira){Cor.RESET}")
    if sem_data:
        print(f"    {Cor.AMARELO}● Sem data legível:        {len(sem_data)}{Cor.RESET}")

    if not plano and not live_videos:
        print(f"\n  {Cor.VERDE}✔ Tudo já está limpo e padronizado! Nada a fazer.{Cor.RESET}\n")
        return

    # Prévia dos vídeos Live Photo
    if live_videos:
        print(f"\n  {Cor.MAGENTA}Vídeos de Live Photo detectados (mover p/ Lixeira):{Cor.RESET}")
        for vid in live_videos[:8]:
            print(f"    {Cor.CINZA}🗑️  {vid['name']}{Cor.RESET}")
        if len(live_videos) > 8:
            print(f"    {Cor.CINZA}... e mais {len(live_videos) - 8} vídeo(s) Live.{Cor.RESET}")

    # Prévia das renomeações
    if plano:
        print(f"\n  {Cor.BOLD}Prévia das renomeações (mostrando até 10):{Cor.RESET}")
        for item in plano[:10]:
            print(f"    {Cor.CINZA}{item['antigo']}{Cor.RESET} ➜ {Cor.VERDE}{item['novo']}{Cor.RESET}")
        if len(plano) > 10:
            print(f"    {Cor.CINZA}... e mais {len(plano) - 10} arquivo(s).{Cor.RESET}")

    lixeira_confirmada = []
    if live_videos:
        resp_live = input(
            f"\n  {Cor.MAGENTA}Mover {len(live_videos)} vídeo(s) de Live Photo para a Lixeira?{Cor.RESET} [S/n]: "
        ).strip().lower()
        if resp_live != "n":
            lixeira_confirmada = live_videos
        else:
            plano, _, _ = planejar_renomeacoes(arquivos)

    confirmar = input(
        f"  {Cor.BOLD}Executar ({len(plano)} renomeações + {len(lixeira_confirmada)} p/ lixeira)?{Cor.RESET} [S/n]: "
    ).strip().lower()

    if confirmar == "n":
        print(f"\n  {Cor.AMARELO}Operação cancelada pelo usuário.{Cor.RESET}\n")
        return

    print()
    inicio = time.perf_counter()
    renomeados_ok, lixeira_ok, erros = executar_lotes(service, plano, lixeira_confirmada)
    duracao = time.perf_counter() - inicio

    print(f"\n{Cor.BOLD}────────────────────────────────────────────────────────{Cor.RESET}")
    print(f"  {Cor.VERDE}🎉 Concluído em {duracao:.1f}s!{Cor.RESET}")
    print(f"     • {renomeados_ok} arquivo(s) renomeado(s)")
    if lixeira_ok:
        print(f"     • {lixeira_ok} vídeo(s) Live Photo movido(s) para a Lixeira")

    if erros:
        print(f"  {Cor.VERMELHO}⚠ {len(erros)} erro(s) durante o processo:{Cor.RESET}")
        for e in erros[:10]:
            print(f"    - {e}")

    print(f"{Cor.BOLD}────────────────────────────────────────────────────────{Cor.RESET}\n")


if __name__ == "__main__":
    main()
