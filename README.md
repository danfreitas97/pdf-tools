# PDF Tools

Um conjunto de ferramentas de PDF que roda **inteiramente na sua máquina**. Sem upload, sem limite de arquivos, sem marca d'água, sem conta, de graça.

A ideia é simples: as tarefas que normalmente levam você a um site tipo iLovePDF não deveriam exigir mandar seus documentos para o servidor de outra pessoa.

## Ferramentas

**Organizar**

| Ferramenta | O que faz |
|---|---|
| **Juntar PDFs** | Combina vários PDFs em um só, na ordem que você definir |
| **Dividir PDF** | Separa um PDF em partes (ao meio, a cada N páginas ou por tamanho) |
| **Páginas** | Extrai, remove ou reordena páginas por intervalo (`1-3, 7, 10-`) |
| **Girar PDF** | Gira páginas com pré-visualização — você vê o resultado antes de salvar |
| **Cortar PDF** | Desenha a área de corte direto sobre a página |

**Converter**

| Ferramenta | O que faz |
|---|---|
| **Imagens para PDF** | Converte JPG, PNG e WebP em um único PDF |
| **PDF para Imagens** | Exporta cada página como imagem |
| **Tons de Cinza** | Converte páginas coloridas para cinza e economiza toner; páginas já em preto e branco ficam intactas, com o texto pesquisável |
| **Comprimir** | Reduz o tamanho por qualidade de imagem e por limite de resolução (300/150/96 dpi) |

**Impressão**

| Ferramenta | O que faz |
|---|---|
| **Adicionar Margens** | Bordas brancas com medida independente nas 4 bordas, modo encadernação (margem interna espelhada) e pré-visualização página a página |
| **Montar Folhas** | 2 páginas por folha, ou livreto com a ordem certa para dobrar e grampear no centro |
| **Verificar Impressão** | Relatório apontando tamanhos de página misturados, imagens de baixa resolução, fontes não embutidas, conteúdo fora da área segura e páginas em branco |

**Segurança**

| Ferramenta | O que faz |
|---|---|
| **Proteger com Senha** | Criptografa em AES-256, com controle de impressão e cópia de texto |
| **Remover Senha** | Tira a proteção de PDFs que você já consegue abrir |
| **Limpar Metadados** | Remove autor, software e histórico de edição antes de enviar o arquivo |

Interface com arrastar e soltar, tema claro/escuro automático, barra de progresso e cancelamento no meio da operação.

## Instalação

### Opção 1 — Executável (recomendado)

Baixe o `PDF Tools.exe` mais recente em [Releases](../../releases). É um arquivo único, não precisa instalar nada — nem Python.

### Opção 2 — Rodar pelo código-fonte

Requer Python 3.11 ou superior (desenvolvido no 3.13).

```bash
git clone https://github.com/danfreitas97/pdf-tools.git
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

[PyMuPDF](https://pymupdf.readthedocs.io/) e [pypdf](https://pypdf.readthedocs.io/) para manipulação dos PDFs, [Pillow](https://python-pillow.org/) para imagens, [cryptography](https://cryptography.io/) para a criptografia AES-256, [CustomTkinter](https://customtkinter.tomschimansky.com/) para a interface e [PyInstaller](https://pyinstaller.org/) para o empacotamento.

## Licença

MIT — veja [LICENSE](LICENSE).
