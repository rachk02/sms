import asyncio

from django.core.management.base import BaseCommand

from apps.core import bus, files


class Command(BaseCommand):
    help = "Déclare les échanges, les douze files et les paliers de réessai. Idempotent."

    def handle(self, *args, **options):
        async def declarer():
            connexion = await bus.connecter()
            async with connexion:
                canal = await connexion.channel()
                await files.declarer(canal)
                await bus.declarer_echange(canal)

        asyncio.run(declarer())
        self.stdout.write(
            f"{len(files.FILES)} files et {len(files.PALIERS_MS)} paliers de réessai déclarés."
        )
