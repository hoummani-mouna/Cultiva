# Corrections et décisions (dans l'ordre de la conversation)

## Données
1. **SRTM** : utiliser `SRTM_Fes_Meknes.csv` (23 cellules, lat 33–34,5 ; lon −5,625 à −3,125), qui couvre bien la grille climatique ; rapprochement par la cellule la plus proche.
2. **pH** : les sols de la grille ont un pH de 7,5 à 8,1 alors que 21 cultures sur 56 ont un pH max < 7,5 → pénalité progressive (0 à 1,5 unité hors plage), aucune culture éliminée.
3. **Données FAO** : nationales ; 13 cultures sans données (score rendement vide) ; lignes groupées marquées « fiabilité faible » ; plusieurs lignes d'une même culture/année sont agrégées (production ÷ surface).
4. **GRACE** : utilisé seulement pour l'étude du stockage d'eau (tendance ≈ −1,5 cm/an au Maroc, −2,4 cm/an sur Fès-Meknès), pas dans le score des rotations.
5. **Évapotranspiration** : calculée (Hargreaves) à partir de POWER ; Kc estimés (0,5 / 0,8 / 1,05).

## Logique de rotation
6. **2 propositions** (et non 3 cultures obligatoires), calculées à partir de la **culture précédente** (et éventuellement de l'avant-dernière) ; longueur réglable de 1 à 3.
7. **Les 2 propositions ne partagent aucune culture** (avant : même cultures dans un autre ordre).
8. **Cultures de quelques mois seulement** : cycle ≤ 365 jours (la luzerne, 4 ans, est exclue).
9. **R01** appliquée aussi entre la culture précédente et la première proposée.
10. **R08 retirée** (plus de seuil de mois, plus de conflit, plus de malus) : jugement d'expert. Le calendrier et la période de **sol nu** restent affichés à titre informatif.
11. **R09/R10/R11** lues dans une table (plus de valeurs codées) ; R09 non cumulée avec R03.
12. **Moteur piloté par CSV** : activation, mode, points et paramètres des règles dans `rotation_rules_moteur.csv` ; poids dans `engine_config.json`.

13. **MODIS** : ajout de la **fragilité de la zone** (sensibilité NDVI/pluie, années hydrologiques complètes 2020-2024), qui augmente le poids de l'eau dans le score des cultures. Le NDVI est une moyenne de cellule (~50 km) : on compare chaque zone à elle-même.
14. **GRACE devient une couche de supervision** (changement climatique) : niveau par zone qui règle le poids de l'eau et durcit R05. L'étude globale Maroc (étude 5) reste produite pour le tableau de bord.

## Limites
- 13 points climatiques d'environ 50 km → recommandation par zone, pas par parcelle.
- Poids, points des règles, Kc et seuils : choix de prototype à valider avec un agronome.
- GRACE : résolution grossière (points voisins aux valeurs identiques) ; les 13 zones sont en « alerte » ou « critique » ; seuils des niveaux = choix de prototype ; ne mesure pas les nappes seules.
- Moteur testé en ligne de commande seulement ; l'intégration web reste à faire.
