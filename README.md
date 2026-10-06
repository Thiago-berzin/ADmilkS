# Viva Leite - Sistema de Gestão (Protótipo)

<img width="1000" height="522" alt="image" src="https://github.com/user-attachments/assets/ac21dbdc-f9de-4a97-acb9-42588c7454c5" />


Este é um protótipo de aplicativo/sistema web desenvolvido para auxiliar na administração e distribuição do programa "Viva Leite"[cite: 2]. O sistema permite que administradores gerenciem os beneficiários e registrem as operações de entrega de leite[cite: 3, 9].

## 🛠️ Estrutura e Tecnologias do Projeto

O projeto segue uma estrutura baseada em Python para o backend e templates HTML/CSS/JS para o frontend[cite: 1]:

* **Backend:** Arquivo principal `app.py` (sugerindo o uso de um framework como Flask)[cite: 1].
* **Frontend (Templates HTML):** Páginas divididas na pasta `templates/`, incluindo `base.html`, `login.html`, `beneficiados.html`, `usuarios.html` e `historico.html`[cite: 1].
* **Estáticos:** Folhas de estilo e scripts organizados em `static/css/style.css` e `static/js/app.js`[cite: 1].

## 📋 Funcionalidades

* **Autenticação:** Tela de login para controle de acesso através de Usuário e Senha[cite: 2].
* **Painel do Administrador:** Visualização da lista completa de beneficiários cadastrados[cite: 3].
* **Gestão de Beneficiários:** 
  * Registro de novos beneficiários informando o Nome, Código de identificação e a opção de ativar uma "Cota Dupla"[cite: 4, 5].
  * Seleção e exclusão de beneficiários do sistema[cite: 7].
* **Operação de Entrega:** Tela específica para a entrega, onde é possível confirmar a quantidade de litros de leite, checar se há cota dupla e coletar a assinatura digital do beneficiário[cite: 9].

## 📱 Telas do Protótipo (Wireframes)

Abaixo estão as interfaces principais desenhadas para o protótipo:

### 1. Tela de Login
Página inicial para acesso ao sistema[cite: 2].

<img width="272" height="587" alt="image" src="https://github.com/user-attachments/assets/d3b61f1b-3501-4e75-84b8-cc656198903b" />


### 2. Lista de Beneficiados
Visão do administrador listando as pessoas cadastradas[cite: 3].

<img width="306" height="665" alt="image" src="https://github.com/user-attachments/assets/3e1b39ef-2345-4471-99b5-b090ebdc1552" />


### 3. Exclusão de Cadastro
Interface para selecionar e remover o cadastro de um beneficiário[cite: 7].

<img width="305" height="662" alt="image" src="https://github.com/user-attachments/assets/b7bc6b5d-7d30-4674-8900-a478ad17b8e4" />


### 4. Operação e Assinatura
Tela de validação da entrega de leite com campo para recolhimento da assinatura[cite: 9].

<img width="270" height="582" alt="image" src="https://github.com/user-attachments/assets/b8f57833-a93b-42bb-9152-62ac26edabc2" />

