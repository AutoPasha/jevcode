"""Shopping cart maths."""


class Cart:
    def __init__(self):
        self.lines = []

    def add(self, name, price, quantity=1):
        self.lines.append({"name": name, "price": price, "quantity": quantity})

    def subtotal(self):
        return sum(line["price"] * line["quantity"] for line in self.lines)

    def total(self, tax_rate=0.0):
        return round(self.subtotal() * (1 + tax_rate), 2)


def cheapest(cart):
    if not cart.lines:
        return None
    return min(cart.lines, key=lambda line: line["price"])["name"]
