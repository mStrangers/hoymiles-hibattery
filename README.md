# Hoymiles HiBattery (MQTT local) pour Home Assistant

Intégration personnalisée pour piloter une batterie **Hoymiles HiBattery / MS-A2**
(testée sur HiBattery 4020 X, firmwares 1.2.4 et 1.3.3) **en local via MQTT**, sans le cloud :

- capteurs de la batterie (SOC, puissances, PV, port EPS, températures, énergies…) ;
- compteurs du jour (production PV, charge, décharge, achat/injection réseau, consommation maison) ;
- 3 modes de pilotage : **Planning** (planning TOU stocké dans la batterie), **Manuel** (décharge à une consigne), **Auto (Hoymiles)** ;
- **plannings TOU modifiables depuis une carte** (sans écrire de code), envoyés et gardés dans la batterie : ils continuent sans Wi-Fi ni cloud ;
- **surveillance** : si la batterie ne suit plus le planning local (par ex. après une validation dans l'app S-Miles), il est renvoyé automatiquement (voir [Surveillance](#surveillance-du-planning)).

## Prérequis

- **Home Assistant 2024.11 ou plus récent** (testé avec 2026.10.3).
- La batterie publie sur le **broker MQTT utilisé par Home Assistant** (option MQTT de l'app S-Miles), topics `homeassistant/.../MSA-xxxxxxxx/...`.
- L'intégration **MQTT** de Home Assistant est configurée.

## Installation (HACS)

1. HACS → menu ⋮ → **Dépôts personnalisés** → `https://github.com/mStrangers/hoymiles-hibattery`, catégorie **Intégration**.
2. Installez **Hoymiles HiBattery (MQTT local)**, puis **redémarrez** Home Assistant.
3. Paramètres → Appareils et services → **Ajouter une intégration** → *Hoymiles HiBattery*.
   - Identifiant : `MSA-280526231099` (celui visible dans les topics MQTT)
   - Préfixe : `homeassistant`
   - Capacité par pack : 4 kWh (HiBattery 4020 X)
   - Compteurs d'énergie achetée / injectée (optionnel, ex. Shelly) : pour les compteurs réseau et la consommation de la maison.
4. La capacité et les compteurs se modifient ensuite dans **Configurer** (un compteur peut être retiré en vidant le champ).

La carte d'édition des plannings est chargée automatiquement (pas de ressource à ajouter).
Après une mise à jour, faites **Ctrl+F5** dans le navigateur.

## Carte des plannings

```yaml
type: custom:hoymiles-tou-card
entity: sensor.msa_280526231099_planning_tou
title: Plannings batterie
```

- Choisir un planning, le **modifier** (mode, heure de fin, SOC, puissances), **couper** un créneau en deux ✂ ou le **supprimer** 🗑 (fusion avec le précédent) : les créneaux restent toujours continus de 00:00 à 24:00.
- **Enregistrer**, **Enregistrer sous…**, renommer, créer, supprimer un planning.
- **Activer** un planning : il est envoyé à la batterie (en mode Planning). Enregistrer le planning actif le renvoie aussi.
- Si la batterie ne répond pas, la carte affiche « Planning enregistré, mais pas envoyé à la batterie » : le planning est
  gardé dans Home Assistant, il suffit de le renvoyer.
- **Lire la batterie** : affiche le planning réellement en mémoire (notification ; uniquement en mode Planning).

Modes de créneau (identiques à l'app S-Miles) :

| Mode | App S-Miles | Réglages utilisés |
|---|---|---|
| 1 | Charge forcée | SOC de fin de charge, puissance de charge |
| 2 | Recharge prioritaire PV | SOC de fin de charge |
| 4 | Autoconsommation | SOC de fin de décharge, puissance de décharge max |

Limites de la batterie : 12 créneaux max, pas de 15 min, puissances 100–1000 W, SOC 10–100 %.

## Surveillance du planning

En mode Planning, l'intégration compare le comportement réel de la batterie au créneau en cours :

| Créneau | Considéré comme non respecté si… |
|---|---|
| Charge forcée | pas de charge depuis le réseau alors que le SOC est sous l'objectif (sauf si le PV suffit), charge bien au-delà de la puissance demandée, ou décharge |
| Recharge prioritaire PV | charge depuis le réseau ou décharge |
| Autoconsommation | charge depuis le réseau (sauf au SOC mini, recharge de protection) ou décharge au-delà du maximum |

- Pas de jugement pendant le chauffage de la batterie ; le courant pris pour les appareils branchés sur la prise EPS est toléré.
- Après **3 min** d'écart : `binary_sensor.msa_…_planning_tou_non_respecte` passe à ON, le planning local est renvoyé
  et une notification est créée.
- Si l'écart persiste, nouvel envoi **au plus toutes les 30 min** (la batterie n'est pas inondée de messages).

## Entités principales

| Entité | Rôle |
|---|---|
| `select.msa_…_mode_batterie` | Planning / Manuel / Auto (Hoymiles) |
| `select.msa_…_planning_tou_choisi` | planning actif |
| `sensor.msa_…_planning_tou` | créneau en cours (attributs : tous les plannings) |
| `binary_sensor.msa_…_planning_tou_non_respecte` | écart > 3 min avec le planning local |
| `sensor.msa_…_puissance_reseau_compteur`, `…_puissance_consommation_maison` | réseau (+ achat) et maison vus par le compteur de la batterie (Shelly Pro 3EM, firmware ≥ 1.3.3) |
| `sensor.msa_…_energie_eps_entree`, `…_energie_eps_sortie` | énergie du jour de la prise EPS (ex. micro-onduleur PV branché dessus) |
| `sensor.msa_…_energie_prise_entree_totale`, `…_sortie_totale`, `…_energie_eps_entree_totale`, `…_sortie_totale` | compteurs totaux (pour le tableau Énergie, firmware ≥ 1.3.3) |
| `number.msa_…_consigne_de_decharge`, `…_decharge_max`, `…_soc_mini` | mode Manuel (consigne renvoyée toutes les 10 s, 0 W sous le SOC mini) |
| `button.msa_…_envoyer_le_planning`, `…_lire_le_planning`, `…_redemarrer` | actions |

## Services

`hoymiles_hibattery.save_plan`, `delete_plan`, `select_plan`, `send_plan`, `read_plan`
(voir Outils de développement → Actions).

- `save_plan`, `select_plan` et `send_plan` échouent avec un message clair si la batterie ne confirme pas l'envoi
  (`send_plan` appelé avec une réponse renvoie plutôt `{"ok": false, "message": …}`).
- En mode Planning, `send_plan` avec le nom d'un autre planning en fait le planning choisi
  (sinon la surveillance remettrait l'ancien).

## Remarques

- En mode `mqtt_ctrl` (mode Manuel), le firmware 1.2.4 **ignore les consignes négatives** (charge) :
  le mode Manuel ne propose donc que la décharge. La **charge forcée fonctionne en mode Planning**.
- Une validation de planning dans l'app S-Miles remplace le planning local : la surveillance le renvoie.

Exemples de cartes : dossier [`examples/`](examples/).
