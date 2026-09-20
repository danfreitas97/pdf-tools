# PDF Tools

Um conjunto de ferramentas de PDF que roda **inteiramente na sua máquina**. Sem upload, sem limite de arquivos, sem marca d'água, sem conta, de graça.

A ideia é simples: as tarefas que normalmente levam você a um site tipo iLovePDF não deveriam exigir mandar seus documentos para o servidor de outra pessoa.

## Ferramentas

| Ferramenta | O que faz |
|---|---|
| **Juntar PDFs** | Combina vários PDFs em um só, na ordem que você definir |
| **Dividir PDF** | Separa um PDF em partes (ao meio, a cada N páginas ou por intervalo) |
| **Comprimir** | Reduz o tamanho do arquivo em três níveis de compressão |
| **Imagens para PDF** | Converte JPG, PNG e WebP em um único PDF |
| **PDF para Imagens** | Exporta cada página como imagem |
| **Girar PDF** | Gira páginas com pré-visualização — você vê o resultado antes de salvar |
| **Cortar PDF** | Desenha a área de corte direto sobre a página |
| **Adicionar Margens** | Insere bordas brancas, útil para impressão e encadernação |

Interface com arrastar e soltar, tema claro/escuro automático, barra de progresso e cancelamento no meio da operação.

## Instalação

### Opção 1 — Executável (recomendado)

Baixe o `PDF Tools.exe` mais recente em [Releases](../../releases). É um arquivo único, não precisa instalar nada — nem Python.

### Opção 2 — Rodar pelo código-fonte

Requer Python 3.11 ou superior (desenvolvido no 3.13).

```bash
git clone https://github.com/<seu-usuario>/pdf-tools.git
cd pdf-tools

python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

pip install -r requirements.txt
python app.py
```

## Gerar o executável

No Windows, com PowerShell:

```powershell
.\build.ps1
```

O script cria um `.venv` isolado, instala as dependências e o PyInstaller, e gera `dist\PDF Tools.exe`. O ambiente isolado é proposital: sem ele, pacotes pesados instalados globalmente (pandas, matplotlib, pyarrow) acabam empacotados dentro do executável.

## Privacidade

Nenhum arquivo sai da sua máquina. O programa não faz requisições de rede. A única coisa que ele grava fora da pasta do projeto são suas preferências de interface, em `%APPDATA%\PDF Tools\settings.json`.

## Tecnologias

[PyMuPDF](https://pymupdf.readthedocs.io/) e [pypdf](https://pypdf.readthedocs.io/) para manipulação dos PDFs, [Pillow](https://python-pillow.org/) para imagens, [CustomTkinter](https://customtkinter.tomschimansky.com/) para a interface e [PyInstaller](https://pyinstaller.org/) para o empacotamento.

## Licença

MIT — veja [LICENSE](LICENSE).
