# Journal de bord du projet

Notes prises au fil de l'eau. Ce fichier n'est pas un livrable : c'est la matière première du rapport technique, en particulier des sections 2 (architecture, choix techniques justifiés) et 11 (difficultés rencontrées).

Règle : une entrée à chaque décision et à chaque blocage, le jour même.

---

## Étape 1 : génération du dataset

### Audit du générateur fourni

Le générateur de l'annexe A a été exécuté tel quel avant toute modification. Quatre incohérences relevées, chiffres à l'appui :

| Constat mesuré | Conséquence sur les analyses demandées |
|---|---|
| Les dates s'étalent du 01/01/2023 au 02/04/2041 | Analyse de saisonnalité mensuelle impossible |
| Prix moyen : Grade A 836 FCFA/kg, Hors grade 840 | L'analyse « prix moyen par qualité » n'a aucun sens |
| 320 couples région-coopérative pour 40 coopératives | La hiérarchie région / coopérative est fausse |
| Les 5 000 planteurs livrent dans les 8 régions | `dim_planteur` n'a aucun attribut stable |

Cause technique des deux premiers points : `pd.date_range(periods=80000, freq='2h')` couvre 18 ans, et `prix_fcfa_kg` est calculé à partir d'un second tirage de qualités indépendant de la colonne `qualite`.

### Décisions de modélisation

**Grain de la table de faits** : une ligne = une pesée. C'est le grain le plus fin disponible ; tout le reste s'obtient par agrégation. `id_pesee` est conservé comme dimension dégénérée.

**Étoile plutôt que flocon** : la table de faits porte directement les trois clés `id_region`, `id_cooperative`, `id_planteur`, malgré la hiérarchie naturelle entre les trois. La redondance est assumée en échange d'une jointure unique pour l'analyse par région. Contrepartie : rien n'interdit techniquement une incohérence entre la région de la pesée et celle de la coopérative, donc cette cohérence devient une règle de validation automatique du pipeline.

**Clés techniques** : identifiants entiers `SERIAL` dans les dimensions, code métier conservé en colonne. Motif : la clé reste stable si un planteur change de coopérative ou si une coopérative est renommée.

**`certifie_bio` déplacé dans `dim_planteur`** : la certification est une propriété de l'exploitation, pas de la livraison. Le générateur d'origine la tirait sur chaque ligne de pesée.

**Mesures et additivité** : `tonnage_kg` et `montant_fcfa` sont additifs, `prix_fcfa_kg` et `humidite_pct` ne le sont pas. Le prix moyen par qualité sera donc calculé en moyenne pondérée par le tonnage, `SUM(montant) / SUM(tonnage)`, et non en `AVG(prix)`.

### Corrections apportées au générateur

1. **Période** : deux campagnes complètes, du 01/10/2022 au 30/09/2024, avec pondération mensuelle réelle. La campagne principale (octobre à mars) représente 70,8 % du tonnage.
2. **Prix** : dérivé du grade réellement attribué, avec prime bio de 10 % et coefficient de 0,96 en campagne intermédiaire. Résultat : Grade A 1 005 FCFA/kg, Grade B 828, Grade C 675, Hors grade 499.
3. **Hiérarchie** : référentiels séparés pour les coopératives (40, rattachées à leur région) et les planteurs (5 000, rattachés à une coopérative). Vérifié : 40 couples région-coopérative, 0 planteur multi-région.
4. **Humidité dérivée de la qualité** : Grade A à 6,6 % en moyenne, Hors grade à 10,2 %, la norme d'exportation étant de 8 %.

### Défauts injectés volontairement

| Défaut | Volume | Situation réelle simulée |
|---|---|---|
| `humidite_pct` manquante | 3 152 lignes (4 %) | capteur défaillant, mesure oubliée |
| `prix_fcfa_kg` à -1 | 1 576 lignes (2 %) | code d'erreur du logiciel de bascule |
| Erreurs de saisie sur les noms | 2 364 lignes (3 %) | région ou coopérative tapée à la main |
| Doublons de scan | 1 200 lignes (1,5 %) | même ticket pesé deux fois |

Les tonnages saisis en tonnes au lieu de kilos ont été écartés : deux familles d'erreurs de saisie suffisent à démontrer la méthode.

### Limites à mentionner dans le rapport

Les données sont synthétiques. Les ordres de grandeur ont été calés sur la filière réelle (poids des régions, campagnes, barème de qualité, norme d'humidité de 8 %), mais le volume total de 24 750 tonnes sur deux ans représente un échantillon de pesées, pas la production nationale, qui dépasse 2 millions de tonnes par an.

### À faire

- [ ] Confirmer auprès de l'enseignant le travail sans binôme
- [ ] Créer le dépôt GitHub public et partager le lien
- [ ] Rédiger la déclaration d'usage des outils d'IA pour l'introduction du rapport
