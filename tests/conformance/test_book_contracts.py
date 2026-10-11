from due.django.conformance.suite import effect_contract_suite
from examples.books.effects import index_book, publish_book
from tests.conformance.book_examples import INDEX_EXAMPLES, PUBLISH_EXAMPLES


@effect_contract_suite(index_book, examples=INDEX_EXAMPLES)
class TestBookIndexing:
    pass


@effect_contract_suite(publish_book, examples=PUBLISH_EXAMPLES)
class TestBookPublication:
    pass
