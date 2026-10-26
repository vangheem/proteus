from proteus import Component

component = Component()


@component.get("/")
def index():
    return component.page("Echo", '<p id="echo-body">echo-v1</p>')


def setup():
    return component
