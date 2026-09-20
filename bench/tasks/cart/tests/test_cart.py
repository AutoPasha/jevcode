import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cart import Cart, cheapest


class TestCart(unittest.TestCase):
    def setUp(self):
        self.cart = Cart()
        self.cart.add("mug", 10.0, 2)
        self.cart.add("book", 5.0)

    def test_subtotal(self):
        self.assertEqual(self.cart.subtotal(), 25.0)

    def test_total_with_tax(self):
        self.assertEqual(self.cart.total(tax_rate=0.2), 30.0)

    def test_cheapest(self):
        self.assertEqual(cheapest(self.cart), "book")

    def test_discount_is_applied_before_tax(self):
        self.assertEqual(self.cart.total(tax_rate=0.2, discount=0.1), 27.0)

    def test_discount_alone(self):
        self.assertEqual(self.cart.total(discount=0.5), 12.5)


if __name__ == "__main__":
    unittest.main()
