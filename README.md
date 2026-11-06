# Eye Track — prancha de CAA

Prancha alfanumérica de Comunicação Aumentativa e Alternativa controlada pelo olhar. O programa usa a webcam para mover o cursor e seleciona uma tecla quando o olhar fica sobre ela por um segundo.

## Preparar no Windows

Abra o Anaconda Prompt ou PowerShell e rode:

```powershell
conda create --override-channels -c conda-forge -n eyetrack python=3.10 pip ipykernel -y
conda activate eyetrack
cd C:\Users\T440\EyeTrack
python -m pip install -r requirements.txt
python -m ipykernel install --user --name eyetrack --display-name "Python (eyetrack)"
```

## Rodar

No VS Code, selecione o interpretador `C:\Users\T440\Miniconda3\envs\eyetrack\python.exe` e execute `main.py`. Para garantir o uso desse ambiente, rode no terminal do PowerShell:

```powershell
& 'C:\Users\T440\Miniconda3\envs\eyetrack\python.exe' main.py
```

A prancha abre em tela cheia e inicia a prévia da webcam automaticamente; o cursor continua parado até pressionar **Iniciar olhar** ou **Calibrar**. A prévia no canto superior mostra a webcam; os círculos verdes marcam as íris detectadas e as linhas amarelas mostram os limites dos olhos. **Câmera** oculta ou mostra essa prévia. Pressione **Calibrar** e acompanhe com os olhos os nove pontos vermelhos apresentados. Tente manter a cabeça imóvel, ficar a uma distância confortável e olhar para o centro de cada ponto até ele avançar. Depois, fixe o olhar em uma tecla por cerca de um segundo para selecioná-la. A calibração vale até fechar o programa. **Esc** fecha a prancha; **F11** alterna a tela cheia. O cursor fica afastado das bordas para evitar acionamentos acidentais do fail-safe do PyAutoGUI.

Há uma prancha de letras e outra de números, sugestões de palavras, teclas **SIM** e **NÃO**, espaço, apagar, limpar, leitura do texto e controle para ligar/desligar a voz. A síntese de voz usa as vozes instaladas no Windows.

## Ajuste do olhar

O mapeamento é aproximado e depende da posição da webcam, iluminação e rosto. Se o cursor não cobrir bem a prancha, ajuste `GAZE_X_RANGE` e `GAZE_Y_RANGE` no início de `main.py`. O tempo de seleção pode ser alterado em `DWELL_SECONDS`.
