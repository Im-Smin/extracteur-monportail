"""Verification manuelle de l'authentification.

Usage : python verifier_connexion.py
Se connecter dans la fenetre qui s'ouvre, puis lire le resultat en console.
"""

from pathlib import Path

from extracteur.auth import SessionNavigateur

session = SessionNavigateur(Path(".session"))
session.ouvrir()

print("Connectez-vous dans la fenetre, puis patientez...")
if not session.attendre_connexion():
    print("ECHEC : connexion non detectee dans le delai imparti.")
    session.fermer()
    raise SystemExit(1)

print("Connexion detectee.")
reponse = session.transport("/ena/site/accueil?idSite=181216")
print(f"GET /ena/site/accueil -> HTTP {reponse.statut}")
print("OK" if reponse.statut == 200 else "ECHEC")
session.fermer()
