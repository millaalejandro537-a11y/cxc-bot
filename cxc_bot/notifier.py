"""Abstraccion de notificaciones. Hoy: consola + log. Manana: n8n puede
reemplazar esto por WhatsApp, correo, Slack, etc. sin tocar runner.py -- el
runner solo llama notify_success()/notify_error() con un mensaje y contexto."""

from __future__ import annotations

import logging
import sys
from abc import ABC, abstractmethod
from typing import Dict, List

logger = logging.getLogger("cxc_bot.notifier")


class Notifier(ABC):
    @abstractmethod
    def notify_success(self, message: str, context: Dict) -> None:
        raise NotImplementedError

    @abstractmethod
    def notify_error(self, message: str, context: Dict) -> None:
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    """Escribe a stderr (no stdout) a proposito: cuando el CLI se llama con
    --json, stdout debe contener SOLO el JSON machine-readable."""

    def notify_success(self, message: str, context: Dict) -> None:
        print(f"[OK] {message}", file=sys.stderr)

    def notify_error(self, message: str, context: Dict) -> None:
        print(f"[ERROR] {message}", file=sys.stderr)


class LogNotifier(Notifier):
    def notify_success(self, message: str, context: Dict) -> None:
        logger.info("%s | contexto=%s", message, context)

    def notify_error(self, message: str, context: Dict) -> None:
        logger.error("%s | contexto=%s", message, context)


class CompositeNotifier(Notifier):
    def __init__(self, notifiers: List[Notifier]):
        self.notifiers = notifiers

    def notify_success(self, message: str, context: Dict) -> None:
        for n in self.notifiers:
            n.notify_success(message, context)

    def notify_error(self, message: str, context: Dict) -> None:
        for n in self.notifiers:
            n.notify_error(message, context)


def default_notifier() -> Notifier:
    return CompositeNotifier([ConsoleNotifier(), LogNotifier()])


# Placeholder documentado para cuando n8n reemplace esto por un canal real
# (WhatsApp, correo, Slack). NO implementar todavia -- requiere credenciales
# e infraestructura que no existen en esta fase.
class WebhookNotifier(Notifier):
    """Futuro: POST a un webhook de n8n, que decide a donde reenviar
    (WhatsApp/correo/etc). Se deja el contrato listo pero sin implementar."""

    def __init__(self, *_, **__):
        raise NotImplementedError(
            "WebhookNotifier no esta implementado todavia. Usa default_notifier() mientras tanto; "
            "n8n puede leer la salida JSON de 'process-due-routes' directamente sin necesitar esto."
        )

    def notify_success(self, message: str, context: Dict) -> None:
        raise NotImplementedError

    def notify_error(self, message: str, context: Dict) -> None:
        raise NotImplementedError
