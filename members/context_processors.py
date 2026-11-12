from .permissions import module_access


def workspace_access(request):
    if not request.user.is_authenticated:
        return {}
    modules = module_access(request.user)
    return {
        "workspace_modules": modules,
        "master_modules": modules[1:],
        "has_master_access": any(module["visible"] for module in modules[1:]),
    }
