import os.path
from tools.helpers import format_value


class A:
    def run(self) -> str:
        return format_value(os.path.basename(__file__))
