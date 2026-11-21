renomeador de fotos do drive

renomeia as fotos do drive pra data que foi tirada (tipo 2024-03-15_14h30.heic) e joga os videozinhos de live photo do iphone na lixeira se quiser

precisa de:
python 3.10+
pip install google-api-python-client google-auth-oauthlib
pasta no drive assim: Fotos/ano/mes (ex: Fotos/2024/03)
credentials.json na mesma pasta do script

pra pegar o credentials.json: google cloud console, cria um projeto, ativa a drive api, cria um id oauth do tipo app pra computador e baixa. coloca teu email como usuario de teste senao nao deixa logar

usar:

python renomeador.py, digita ano e mes, confirma

na primeira vez abre o navegador pra logar

obs: video e print nao tem data na foto entao ele usa a data que subiu pro drive
