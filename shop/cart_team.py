"""Your cart. Complete every TODO until `python manage.py test` is green.

The rules (money, stock, coupons, checkout) are in README.md and in shop/domain.py. Do not change
shop/domain.py or the tests. Money is Decimal (never float) and the price always comes from the
catalog, never from the request.
"""
from decimal import Decimal

from shop.domain import (
    COUPONS,
    IGV_RATE,
    CartError,
    InvalidCoupon,
    InvalidQuantity,
    Line,
    OutOfStock,
    UnknownProduct,
    fmt,
    money,
)

ZERO = Decimal("0")


def _check_qty(qty):
    """A quantity is an int >= 1. True/False are ints in Python: they are not quantities."""
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
        raise InvalidQuantity(f"Cantidad no válida: {qty!r}")


class Cart:
    def __init__(self, catalog):
        self._catalog = catalog  # sku -> Product (price Decimal, stock int)
        self._qty = {}  # sku -> quantity, in insertion order
        self._coupon = None  # one coupon per cart

    # --- persistence between requests (already done) ---
    def to_state(self):
        return {"qty": dict(self._qty), "coupon": self._coupon}

    @classmethod
    def from_state(cls, catalog, state):
        cart = cls(catalog)
        cart._qty = {sku: int(q) for sku, q in state.get("qty", {}).items() if sku in catalog}
        cart._coupon = state.get("coupon")
        return cart

    # --- lines ---
    def add(self, sku, qty=1):
        """Add qty units of sku. The price is never a parameter: it comes from the catalog."""
        _check_qty(qty)
        product = self._catalog.get(sku)
        if product is None:
            raise UnknownProduct(f"No existe el producto {sku!r}")
        wanted = self._qty.get(sku, 0) + qty  # what is already there PLUS what is added
        if wanted > product.stock:
            raise OutOfStock(
                f"{sku}: pides {wanted} en total y solo quedan {product.stock}"
            )
        self._qty[sku] = wanted

    def remove(self, sku, qty=None):
        """qty=None removes the whole line; otherwise subtract and drop the line at zero."""
        if sku not in self._qty:
            raise UnknownProduct(f"{sku!r} no está en el carrito")
        if qty is None:
            del self._qty[sku]
            return
        _check_qty(qty)
        left = self._qty[sku] - qty
        if left <= 0:  # never a negative quantity: the line simply disappears
            del self._qty[sku]
        else:
            self._qty[sku] = left

    def qty_of(self, sku):
        return self._qty.get(sku, 0)

    def lines(self):
        """One Line per sku, in insertion order, always with the catalog price."""
        return [
            Line(sku, self._catalog[sku].name, self._catalog[sku].price, qty)
            for sku, qty in self._qty.items()
        ]

    # --- coupon and totals ---
    def apply_coupon(self, code):
        """One coupon per cart: applying again (the same or another one) REPLACES it."""
        normalized = (code or "").strip().upper()
        if normalized not in COUPONS:
            raise InvalidCoupon(f"Cupón desconocido: {code!r}")
        self._coupon = normalized

    def subtotal(self):
        return money(sum((line.total for line in self.lines()), ZERO))

    def discount(self):
        if self._coupon is None:
            return money(ZERO)
        kind, amount = COUPONS[self._coupon]
        subtotal = self.subtotal()
        if kind == "percent":
            return money(subtotal * amount / 100)
        return money(min(amount, subtotal))

    def igv(self):
        return money((self.subtotal() - self.discount()) * IGV_RATE)

    def total(self):
        return (self.subtotal() - self.discount()) + self.igv()

    def summary(self):
        return {
            "lines": [
                {
                    "sku": line.sku,
                    "name": line.name,
                    "unit_price": fmt(line.unit_price),
                    "qty": line.qty,
                    "line_total": fmt(line.total),
                }
                for line in self.lines()
            ],
            "coupon": self._coupon,
            "subtotal": fmt(self.subtotal()),
            "discount": fmt(self.discount()),
            "igv": fmt(self.igv()),
            "total": fmt(self.total()),
        }

    # --- checkout ---
    def checkout(self):
        """Check the stock of EVERY line again before touching any of them, then charge."""
        if not self._qty:
            raise CartError("El carrito está vacío")
        for sku, qty in self._qty.items():
            available = self._catalog[sku].stock
            if qty > available:
                raise OutOfStock(f"{sku}: pides {qty} y solo quedan {available}")
        order = self.summary()
        for sku, qty in self._qty.items():
            self._catalog[sku].stock -= qty
        self._qty = {}
        self._coupon = None
        return order


def add_from_payload(cart, payload):
    """Translate the untrusted JSON body of POST /api/cart/items/ into a cart.add call.

    The price is NOT read: the catalog is the only source of prices.
    """
    if not isinstance(payload, dict):
        raise CartError("El cuerpo debe ser un objeto JSON")
    sku = payload.get("sku")
    if not isinstance(sku, str):
        raise CartError("Falta el campo «sku» o no es texto")
    cart.add(sku, payload.get("qty", 1))
