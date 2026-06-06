"""Calculator tool — basic math operations."""
def run(**kwargs):
    expr = kwargs.get('expr', '0')
    return {'result': eval(expr)}
