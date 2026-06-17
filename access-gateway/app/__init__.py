"""access-gateway — proxy/middleware identity-aware d'onix (cloisonnement RBAC FOSS).

S'intercale DEVANT la recherche Onyx : récupère les groupes Microsoft Entra de
l'utilisateur (claims OIDC ou Microsoft Graph `transitiveMemberOf`), les traduit
en **Document Sets** Onyx autorisés via un mapping groupe→DocumentSet, et n'expose
à la recherche QUE ces Document Sets (deny-by-default).

Cible le « trou FOSS » : en édition gratuite, l'index Onyx est partagé (pas de
trimming par utilisateur natif — réservé à l'Enterprise Edition). Ce composant
réintroduit un cloisonnement **au niveau groupe / Document Set** côté passerelle.

Granularité honnête : par GROUPE et par DOCUMENT SET — PAS par document comme
l'OBO/permission-sync EE. Voir ../../docs/RBAC.md.
"""

__version__ = "0.1.0"
