"""Portal web CxC (MVP): API de solo lectura sobre snapshots + reglas del semaforo gerencial.

No reimplementa el calculo de dias de atraso: reutiliza `cxc_bot.payment_behavior.assess_invoice`.
No escribe en SAP ni en Cosprobell BD. Ver docs/PORTAL-MVP.md.
"""
