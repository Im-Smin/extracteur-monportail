"""Verification manuelle de l'authentification.

Usage : python verifier_connexion.py
Se connecter dans la fenetre qui s'ouvre, puis lire le resultat en console.

Chaque execution ouvre un profil de navigateur neuf (voir SessionNavigateur
dans extracteur/auth.py) : une authentification complete est demandee a
chaque lancement de ce script, aucun profil n'est conserve d'une execution a
l'autre.
"""

from extracteur.auth import SessionNavigateur

session = SessionNavigateur()
session.ouvrir()

# try/finally : si l'utilisateur ferme la fenetre ou qu'une exception survient,
# fermer() doit quand meme etre appele (sinon le processus pilote Playwright
# peut rester actif).
try:
    print("Connectez-vous dans la fenetre, puis patientez...")
    if not session.attendre_connexion():
        print("ECHEC : connexion non detectee dans le delai imparti.")
        raise SystemExit(1)

    print("Connexion detectee.")
    reponse = session.transport("/ena/site/accueil?idSite=181216")
    print(f"GET /ena/site/accueil -> HTTP {reponse.statut}")
    print("OK" if reponse.statut == 200 else "ECHEC")
finally:
    session.fermer()
