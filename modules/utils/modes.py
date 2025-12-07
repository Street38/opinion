from dataclasses import dataclass
from inquirer import prompt, List
from inquirer.themes import load_theme_from_dict

from ..retry import SoftError

from settings import SELL_SETTINGS, LIMIT_HOLDING, BID_SETTINGS


@dataclass
class Mode:
    soft_id: int
    text: str
    type: str
    is_new: bool = False
    is_numeric: bool = True

    def __str__(self) -> str:
        return ("⭐️ NEW | " if self.is_new else "") + self.text


def choose_mode():
    def ask_question(question: str, modes: list):
        total_numerics = 0
        choices = []
        for mode in modes:
            mode_numeric = ""
            if mode.is_numeric:
                total_numerics += 1
                mode_numeric = f"{total_numerics}. "

            choices.append((f"{mode_numeric}{mode}", mode.soft_id))

        questions = [
            List(
                name='custom_question',
                message=question,
                choices=choices,
                carousel=True,
            )
        ]

        raw_answer = prompt(
            questions=questions,
            raise_keyboard_interrupt=True,
            theme=THEME,
        )
        return next((mode for mode in modes if mode.soft_id == raw_answer['custom_question']))


    sell_list = []
    if SELL_SETTINGS["close_positions"]:
        sell_list.append("Sell All Positions")
    if SELL_SETTINGS["cancel_orders"]:
        sell_list.append("Close orders")
    if sell_list:
        sell_mode = " & ".join(sell_list)
    else:
        sell_mode = "Sell Nothing"

    if LIMIT_HOLDING["side"] == "buy":
        holding_name = "(Buy)"
    elif LIMIT_HOLDING["side"] == "sell":
        holding_name = f"(Sell {len(BID_SETTINGS['SINGLE_BUY'])} position{'s' if len(BID_SETTINGS['SINGLE_BUY']) != 1 else ''})"
    else:
        raise SoftError(f'Unsupported LIMIT_HOLDING side "{LIMIT_HOLDING["side"]}"')

    answer = ask_question(
        question="🚀 Choose mode",
        modes=[
            Mode(soft_id=0, type="", text="(Re)Create Database", is_numeric=False),
            Mode(soft_id=1, type="module", text=f"Single mode"),
            Mode(soft_id=4, type="module", text=f"Pairs mode"),
            Mode(soft_id=5, type="module", text=f"Limit Holding {holding_name}"),
            Mode(soft_id=2, type="module", text=sell_mode),
            Mode(soft_id=3, type="module", text=f"Parse"),
        ]
    )

    if answer.soft_id == 0:
        answer = ask_question(
            question="💾 You want to delete current and create new database?",
            modes=[
                Mode(soft_id=-1, type="", text="← Exit", is_numeric=False),
                Mode(soft_id=101, type="database",  text="Create new single database", is_numeric=False),
                Mode(soft_id=102, type="database",  text="Create new groups database", is_numeric=False),
            ]
        )

    return answer


THEME = load_theme_from_dict({"List": {
    "selection_cursor": "👉🏻",
    # "selection_color": "violetred1",
}})
