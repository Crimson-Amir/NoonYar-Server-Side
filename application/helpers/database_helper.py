class BreadDoesNotExist(Exception):
    """Custom exception for bread does not exist in bakery-bread table"""
    def __init__(self, message="Bread does not exist in bakery-bread table"):
        self.message = message
        super().__init__(self.message)
