from omnipath_build.utils.cv_term_enums import InteractionTypeCv
from ._mitab_support import mitab_to_silver_interaction

__all__ = [
    'intact_interactions',
]


def intact_interactions():
    """
    Yields IntAct physical interactions as SilverInteraction objects.
    """
    from pypath.inputs.new_intact import intact_interactions as pypath_interactions

    for record in pypath_interactions():
        yield mitab_to_silver_interaction(
            record,
            source='intact',
            fallback_interaction_type=InteractionTypeCv.PHYSICAL_ASSOCIATION,
        )
