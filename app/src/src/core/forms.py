from django import forms

class SubmitTaskForm(forms.Form):
    zip_file = forms.FileField(required=True, label='Select a .zip file', allow_empty_file=False)

    def clean_zip_file(self):
        zip_file = self.cleaned_data.get('zip_file')
        if zip_file and not zip_file.name.endswith('.zip'):
            raise forms.ValidationError("Please select a valid .zip file.")
        return zip_file
