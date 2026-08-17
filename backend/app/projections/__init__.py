"""Projection providers other than ESPN.

Every provider's job is the same: emit raw stat lines keyed by ESPN stat id, so
the scoring engine can re-score them under any league's rules. Nothing here
returns point totals -- a point total from another site is scored under *their*
assumed rules, which is exactly what this app exists to avoid.
"""
