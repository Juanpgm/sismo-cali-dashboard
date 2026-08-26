"""One router module per legacy `api/*.js` function, same route paths.

`health.py` is the only router mounted in slice 1; every other module in this
package lands in its own migration slice (see tasks.md phases 2-8).
"""
