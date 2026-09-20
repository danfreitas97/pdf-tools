# PDF Tools

**Ferramentas de PDF que funcionam no seu computador. De graça, sem limite e sem enviar seus arquivos para lugar nenhum.**

Sabe quando você precisa juntar dois PDFs, ou o arquivo está grande demais para o e-mail? A saída costuma ser um daqueles sites que pedem para você subir o documento. Só que esse documento pode ser um contrato, um holerite, um exame médico — e ele vai parar no servidor de um desconhecido, muitas vezes com limite de dois arquivos por dia e propaganda no meio.

Este programa faz o mesmo serviço, mas **tudo acontece dentro da sua máquina**. Nada sobe para a internet. Não tem cadastro, não tem mensalidade, não tem limite de arquivos nem marca d'água forçada no resultado.

## Como começar

1. Baixe o arquivo **`PDF Tools.exe`** na página de [Releases](../../releases)
2. Dê dois cliques nele
3. Pronto

Não precisa instalar nada, nem baixar qualquer outro programa. É um arquivo único: dá para deixar na área de trabalho, num pen drive ou numa pasta de rede.

> **O Windows mostrou um aviso azul de "aplicativo não reconhecido"?**
> Isso é normal e acontece com todo programa pequeno que não paga por um certificado digital (que custa algumas centenas de reais por ano). Clique em **Mais informações** e depois em **Executar assim mesmo**. Se preferir conferir antes, todo o código está aqui neste repositório, aberto para qualquer um ler.

## O que dá para fazer

### Organizar documentos

| | |
|---|---|
| **Juntar PDFs** | Vários arquivos viram um só, na ordem que você escolher. O resultado já vem com um índice, com o nome de cada documento que entrou |
| **Dividir PDF** | Separa um arquivo grande em partes menores |
| **Páginas** | Tira as páginas que você não quer, separa só as que interessam, ou muda a ordem delas |
| **Numerar Páginas** | Coloca o número em cada página. Dá para pular a capa e escolher onde o número aparece |
| **Girar PDF** | Endireita páginas que foram digitalizadas de lado. Você vê como vai ficar antes de salvar |
| **Cortar PDF** | Recorta só o pedaço da página que interessa, desenhando com o mouse |

### Converter e reduzir

| | |
|---|---|
| **Imagens para PDF** | Junta fotos, prints e digitalizações em um PDF só |
| **PDF para Imagens** | Transforma cada página do PDF em uma imagem |
| **Tons de Cinza** | Tira as cores para gastar menos tinta na impressão |
| **Comprimir** | Deixa o arquivo mais leve, para caber no e-mail ou no site que só aceita 5 MB |

### Preparar para imprimir

| | |
|---|---|
| **Adicionar Margens** | Aumenta a borda branca da página. Tem um modo para documentos que vão ser encadernados, em que a margem de dentro é maior e troca de lado a cada página |
| **Montar Folhas** | Coloca duas páginas em cada folha (economiza metade do papel) ou monta um livreto, já na ordem certa para dobrar e grampear no meio |
| **Verificar Impressão** | Avisa dos problemas antes de você gastar papel: páginas de tamanhos diferentes no mesmo arquivo, imagens que vão sair borradas, texto colado demais na borda |

### Proteger documentos

| | |
|---|---|
| **Proteger com Senha** | Tranca o arquivo com uma senha, e você escolhe se quem abrir pode imprimir ou copiar o texto |
| **Remover Senha** | Tira a senha de um arquivo protegido, desde que você saiba qual é |
| **Marca d'Água** | Carimba CONFIDENCIAL, CÓPIA, RASCUNHO ou o texto que você quiser por cima das páginas |
| **Limpar Metadados** | Todo PDF guarda escondido o nome de quem criou e com qual programa. Isso apaga esses rastros antes de você mandar o arquivo para fora |

Você pode arrastar os arquivos direto para dentro do programa, trabalhar com vários de uma vez, e cancelar no meio se mudar de ideia. Tem tema claro e escuro.

## Perguntas comuns

**Meus arquivos vão para a internet?**
Não. O programa não se conecta a lugar nenhum. Seus documentos não saem do computador.

**Funciona sem internet?**
Funciona. Depois de baixar, você pode até desligar a rede.

**Tem limite de arquivos ou de tamanho?**
Não. Pode processar cem arquivos de uma vez se o seu computador der conta.

**É grátis de verdade? Tem pegadinha?**
É grátis, sem versão paga, sem anúncio e sem pedir cadastro. O código é aberto: qualquer pessoa pode verificar o que ele faz.

**O programa mexe nos meus arquivos originais?**
Não. Ele sempre cria arquivos novos, com um nome diferente. O original fica como estava.

**Funciona em Mac ou Linux?**
O arquivo pronto (.exe) é só para Windows. Em outros sistemas dá para rodar pelo código-fonte (instruções no final).

**Achei um problema ou queria que fizesse outra coisa.**
Abra um chamado em [Issues](../../issues) contando o que aconteceu.

## Licença

Livre para usar, copiar e modificar, inclusive em empresa. Veja [LICENSE](LICENSE).

---

## Para quem programa

Feito em Python com PyMuPDF, pypdf, Pillow e CustomTkinter, empacotado com PyInstaller.

```bash
git clone https://github.com/danfreitas97/pdf-tools.git
cd pdf-tools
python -m venv .venv
.venv\Scripts\activate          # no Windows
pip install -r requirements.txt
python app.py
```

Para gerar o `.exe`, com PowerShell no Windows: `.\build.ps1`. O script cria um ambiente virtual isolado, instala as dependências em versões fixas e monta o executável em `dist\`. O isolamento é proposital: sem ele, bibliotecas pesadas instaladas globalmente acabam dentro do arquivo final.

As preferências de interface ficam em `%APPDATA%\PDF Tools\settings.json`. Senhas nunca são gravadas.
